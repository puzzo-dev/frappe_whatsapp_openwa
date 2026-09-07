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

	now = frappe.utils.now_datetime()
	sessions = frappe.get_all(
		"OpenWA Session",
		filters={
			"status": "Disconnected",
			"gateway_session_id": ["is", "set"],
			"skip_auto_heal": 0,
		},
		fields=["name", "gateway_session_id", "restart_attempt_count", "disconnect_grace_until"],
	)
	if not sessions:
		return

	from frappe_whatsapp_openwa.utils.gateway import (
		STATUS_CONNECTED,
		fetch_session,
		get_gateway_client,
		map_gateway_status,
	)

	client = get_gateway_client(timeout=35.0)

	MAX_RESTART_ATTEMPTS = 3

	for s in sessions:
		# Grace period: don't restart immediately on disconnect
		grace = s.disconnect_grace_until
		if grace and now < frappe.utils.get_datetime(grace):
			continue

		# Corroborate the disconnect against the gateway before acting on it.
		# The local "Disconnected" status is set from a webhook, and the webhook
		# signature covers only the body — it carries no timestamp or nonce, so a
		# captured session.disconnected request stays valid forever and can be
		# replayed to mark a healthy session as down. Restarting on that unverified
		# signal is what turns a replay into a real outage: the restart genuinely
		# drops a live WhatsApp session, and three of them escalate it to Failed.
		# Asking the gateway for the truth costs one GET on a path that only runs
		# for already-disconnected sessions.
		#
		# Only a positive "connected" answer suppresses the restart. A timeout or
		# an unreachable gateway returns None and we fall through to the previous
		# behaviour, so a genuinely disconnected session is still healed.
		gw = fetch_session(client, s.gateway_session_id)
		if gw is not None and map_gateway_status(gw.get("status")) == STATUS_CONNECTED:
			frappe.db.set_value(
				"OpenWA Session",
				s.name,
				{
					"status": STATUS_CONNECTED,
					"last_state_change": now,
					"requires_human_attention": 0,
					"consecutive_disconnect_count": 0,
				},
				update_modified=False,
			)
			continue

		attempts = s.restart_attempt_count or 0
		if attempts >= MAX_RESTART_ATTEMPTS:
			frappe.db.set_value(
				"OpenWA Session",
				s.name,
				{
					"status": "Failed",
					"requires_human_attention": 1,
					"last_state_change": now,
				},
				update_modified=False,
			)
			continue

		try:
			if _restart_on_gateway(client, s.gateway_session_id):
				frappe.db.set_value(
					"OpenWA Session",
					s.name,
					{
						"restart_attempt_count": attempts + 1,
						"last_restart_at": now,
					},
					update_modified=False,
				)
		except Exception as e:
			frappe.log_error(
				f"OpenWA auto-restart failed for {s.name} (attempt {attempts + 1}): {e}",
				"OpenWA Self-Healer",
			)


def _restart_on_gateway(client, gateway_session_id: str) -> bool:
	"""Restart a session on the gateway. Returns True on success.

	The gateway has no /restart route: POST /:id/start boots a stopped engine
	(400 when one is already loaded), so a loaded-but-disconnected engine is
	first stopped, then started.
	"""
	resp = client.post(f"/api/sessions/{gateway_session_id}/start")
	if resp.status_code == 200:
		return True
	if resp.status_code == 400:
		# Engine still loaded — stop it, then start again.
		stop = client.post(f"/api/sessions/{gateway_session_id}/stop")
		if stop.status_code != 200:
			return False
		return client.post(f"/api/sessions/{gateway_session_id}/start").status_code == 200
	return False
