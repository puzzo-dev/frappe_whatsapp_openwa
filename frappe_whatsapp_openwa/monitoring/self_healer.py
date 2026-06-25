import frappe


def heal_disconnected_sessions():
	"""Every 5 minutes: attempt restart on Disconnected sessions past their grace window."""
	from frappe_whatsapp_openwa.utils.cron import acquire_cron_lock, release_cron_lock

	if not acquire_cron_lock("heal_disconnected", ttl_seconds=290):
		return

	try:
		_do_heal()
	finally:
		release_cron_lock("heal_disconnected")


def _do_heal():
	try:
		settings = frappe.get_single("OpenWA Gateway Settings")
		if not settings.enable_openwa_provider:
			return
	except Exception:
		return

	now = frappe.utils.now()

	# NULL disconnect_grace_until is treated as elapsed (reconnect immediately).
	candidates = frappe.db.sql(
		"""SELECT name FROM `tabOpenWA Session`
		   WHERE status = 'Disconnected'
		   AND (restart_attempt_count IS NULL OR restart_attempt_count < 3)
		   AND (disconnect_grace_until IS NULL OR disconnect_grace_until <= %s)""",
		now,
		as_dict=True,
	)

	# Build a single HTTP client for all restart attempts in this tick —
	# avoids opening a new TCP connection per session.
	import httpx
	client = httpx.Client(
		base_url=settings.gateway_base_url,
		headers={"Authorization": f"Bearer {settings.get_password('gateway_api_key')}"},
		timeout=5.0,
	)

	for row in candidates:
		name = row["name"]
		session = frappe.get_doc("OpenWA Session", name)

		# Increment BEFORE attempting restart so a permanently-down gateway
		# still advances the counter and eventually escalates to Restart Failed.
		session.restart_attempt_count = (session.restart_attempt_count or 0) + 1
		session.last_state_change = frappe.utils.now()

		try:
			_issue_restart(session, client)
		except Exception as e:
			frappe.log_error(
				f"Self-heal restart failed for {name} (attempt {session.restart_attempt_count}): {e}",
				"OpenWA Self-Healer",
			)

		session.save(ignore_permissions=True)

	# Sessions that exhausted their attempts → Restart Failed + human alert.
	exhausted = frappe.get_all(
		"OpenWA Session",
		filters={"status": "Disconnected", "restart_attempt_count": [">=", 3]},
		pluck="name",
	)
	for name in exhausted:
		session = frappe.get_doc("OpenWA Session", name)
		session.status = "Restart Failed"
		session.requires_human_attention = 1
		session.last_state_change = frappe.utils.now()
		session.save(ignore_permissions=True)


def _issue_restart(session, client=None):
	import httpx
	if client is None:
		settings = frappe.get_single("OpenWA Gateway Settings")
		client = httpx.Client(
			base_url=settings.gateway_base_url,
			headers={"Authorization": f"Bearer {settings.get_password('gateway_api_key')}"},
			timeout=5.0,
		)
	resp = client.post(f"/api/sessions/{session.gateway_session_id}/restart")
	resp.raise_for_status()
