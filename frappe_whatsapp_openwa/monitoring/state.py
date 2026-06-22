import frappe


def handle_session_event(payload: dict):
	"""Handle push session lifecycle events from the OpenWA gateway webhook."""
	event = payload.get("event", "")
	session_id = payload.get("sessionId", "")

	sessions = frappe.get_all(
		"OpenWA Session",
		filters={"gateway_session_id": session_id},
		pluck="name",
	)
	if not sessions:
		frappe.log_error(
			f"Received {event} for unknown gateway session '{session_id}'",
			"OpenWA State",
		)
		return

	session = frappe.get_doc("OpenWA Session", sessions[0])
	prev_status = session.status

	if event == "session.qr":
		session.status = "QR Required"
		session.qr_code_data = payload.get("data", {}).get("qrCode", "")
	elif event == "session.connected":
		session.status = "Connected"
		session.qr_code_data = ""
		session.restart_attempt_count = 0
		session.consecutive_disconnect_count = 0
	elif event == "session.disconnected":
		session.status = "Disconnected"
		session.disconnect_grace_until = frappe.utils.add_to_date(
			frappe.utils.now(), seconds=30
		)
		session.consecutive_disconnect_count = (session.consecutive_disconnect_count or 0) + 1
	elif event == "session.banned":
		session.status = "Banned"
		session.last_banned_at = frappe.utils.now()
	elif event == "session.restart_failed":
		session.status = "Restart Failed"
	else:
		return

	session.last_state_change = frappe.utils.now()
	session.save(ignore_permissions=True)

	frappe.cache().delete(f"openwa:session:status:{session.name}")

	if session.status != prev_status:
		frappe.publish_realtime(
			"openwa_session_state_change",
			{
				"session_name": session.name,
				"status": session.status,
				"qr_code_data": session.qr_code_data or "",
				"requires_human_attention": session.requires_human_attention,
			},
			after_commit=True,
		)
