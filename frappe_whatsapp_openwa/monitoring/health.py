import frappe


def ping_all_sessions():
	"""Safety-net poll: refresh session status from gateway every minute.

	Uses a cron lock so overlapping scheduler runs don't cause concurrent
	HTTP floods against the gateway. Sessions are pinged using a single
	persistent httpx client (connection pooling, one TCP connection reused).
	"""
	from frappe_whatsapp_openwa.utils.cron import acquire_cron_lock, release_cron_lock

	if not acquire_cron_lock("ping_all_sessions", ttl_seconds=55):
		return

	try:
		_do_ping()
	finally:
		release_cron_lock("ping_all_sessions")


def _do_ping():
	sessions = frappe.get_all(
		"OpenWA Session",
		filters={"status": ["!=", "Banned"]},
		fields=["name", "gateway_session_id"],
	)
	if not sessions:
		return

	try:
		settings = frappe.get_single("OpenWA Gateway Settings")
		if not settings.enable_openwa_provider:
			return
	except Exception:
		return

	import httpx

	# Single client — reuses TCP connection across all sessions (keeps it fast).
	client = httpx.Client(
		base_url=settings.gateway_base_url,
		headers={"Authorization": f"Bearer {settings.get_password('gateway_api_key')}"},
		timeout=3.0,
	)

	ttl = settings.session_health_ttl_seconds or 60

	for session_info in sessions:
		session_name = session_info["name"]
		gw_id = session_info.get("gateway_session_id") or session_name
		try:
			resp = client.get(f"/api/sessions/{gw_id}/status")
			data = resp.json()
			new_status = data.get("status")
			qr = data.get("qrCode")

			# Only load and save the doc if something changed — avoids
			# unnecessary DB writes every minute when everything is stable.
			doc = frappe.get_doc("OpenWA Session", session_name)
			changed = new_status and new_status != doc.status
			if changed:
				doc.status = new_status
				doc.last_state_change = frappe.utils.now()
			if new_status == "QR Required" and qr:
				doc.qr_code_data = qr
			doc.last_health_check = frappe.utils.now()
			doc.save(ignore_permissions=True)

			frappe.cache().setex(
				f"openwa:session:status:{session_name}",
				ttl,
				new_status or "",
			)
		except Exception as e:
			frappe.log_error(
				f"OpenWA health check failed for {session_name}: {e}", "OpenWA Health"
			)
