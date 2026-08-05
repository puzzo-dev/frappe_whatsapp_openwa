import frappe


def ping_all_sessions():
	"""Safety-net poll: refresh session status from gateway every 5 minutes.

	Uses a cron lock so overlapping scheduler runs don't cause concurrent
	HTTP floods against the gateway. Sessions are pinged using a single
	persistent httpx client (connection pooling, one TCP connection reused).
	"""
	from frappe_whatsapp_openwa.utils.cron import acquire_cron_lock, release_cron_lock

	if not acquire_cron_lock("ping_all_sessions", ttl_seconds=290):
		return

	try:
		_do_ping()
	finally:
		release_cron_lock("ping_all_sessions")


def _do_ping():
	sessions = frappe.get_all(
		"OpenWA Session",
		filters={"gateway_session_id": ["is", "set"]},
		fields=["name", "gateway_session_id", "status"],
	)
	if not sessions:
		return

	try:
		settings = frappe.get_single("OpenWA Gateway Settings")
		if not settings.enable_openwa_provider:
			return
	except Exception:
		return

	from frappe_whatsapp_openwa.utils.gateway import (
		STATUS_QR_REQUIRED,
		fetch_qr_image,
		get_gateway_client,
		map_gateway_status,
	)

	# Single client — reuses TCP connection across all sessions (keeps it fast).
	client = get_gateway_client(timeout=3.0)

	ttl = settings.session_health_ttl_seconds or 60
	now = frappe.utils.now()

	for session_info in sessions:
		session_name = session_info["name"]
		current_status = session_info.get("status")
		gw_id = session_info["gateway_session_id"]
		try:
			resp = client.get(f"/api/sessions/{gw_id}")
			if resp.status_code != 200:
				continue
			data = resp.json()
			new_status = map_gateway_status(data.get("status"))

			# Build targeted update dict — avoids loading the full Document object
			# and eliminates the N get_doc + N save pattern that was here before.
			updates = {"last_health_check": now, "last_error": data.get("lastError") or ""}
			if new_status != current_status:
				updates["status"] = new_status
				updates["last_state_change"] = now
				# db.set_value bypasses before_save — keep the attention flag in sync.
				updates["requires_human_attention"] = (
					1 if new_status in (STATUS_QR_REQUIRED, "Failed") else 0
				)
			if new_status == STATUS_QR_REQUIRED:
				# QR codes rotate every ~20s — always pull the fresh image.
				qr = fetch_qr_image(client, gw_id)
				if qr:
					updates["qr_code_data"] = qr
			elif current_status == STATUS_QR_REQUIRED:
				updates["qr_code_data"] = ""

			frappe.db.set_value("OpenWA Session", session_name, updates, update_modified=False)

			from frappe_whatsapp_openwa.utils.cache import set_cached_session_status

			set_cached_session_status(session_name, new_status, ttl)
		except Exception as e:
			frappe.log_error(
				f"OpenWA health check failed for {session_name}: {e}", "OpenWA Health"
			)
