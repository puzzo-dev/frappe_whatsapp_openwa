import frappe

from frappe_whatsapp_openwa.utils.gateway import (
	STATUS_CONNECTED,
	STATUS_DISCONNECTED,
	STATUS_QR_REQUIRED,
	fetch_qr_image,
	get_gateway_client,
	map_gateway_status,
)


def handle_session_event(payload: dict):
	"""Handle push session lifecycle events from the OpenWA gateway webhook.

	Gateway event reference (rmyndharis/OpenWA):
	  session.qr            data = {sessionId, qr}           — raw QR string
	  session.authenticated data = {sessionId, phone, pushName}
	  session.disconnected  data = {sessionId, reason}
	  session.status        data = {sessionId, status}       — lowercase enum
	  session.reconnect_loop data = {sessionId, attempts, nextDelayMs}
	"""
	event = payload.get("event", "")
	data = payload.get("data") or {}
	session_id = payload.get("sessionId") or data.get("sessionId", "")

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
		session.status = STATUS_QR_REQUIRED
		# The webhook carries the raw QR string — the scannable PNG is fetched
		# from the gateway API instead.
		qr_image = _fetch_qr_from_gateway(session.gateway_session_id)
		if qr_image:
			session.qr_code_data = qr_image
	elif event == "session.authenticated":
		session.status = STATUS_CONNECTED
		session.qr_code_data = ""
		session.last_error = ""
		session.restart_attempt_count = 0
		session.consecutive_disconnect_count = 0
	elif event == "session.disconnected":
		session.status = STATUS_DISCONNECTED
		session.disconnect_grace_until = frappe.utils.add_to_date(
			frappe.utils.now(), seconds=30
		)
		session.consecutive_disconnect_count = (session.consecutive_disconnect_count or 0) + 1
	elif event == "session.status":
		session.status = map_gateway_status(data.get("status"))
		if session.status == STATUS_QR_REQUIRED:
			qr_image = _fetch_qr_from_gateway(session.gateway_session_id)
			if qr_image:
				session.qr_code_data = qr_image
		elif session.status != STATUS_QR_REQUIRED:
			session.qr_code_data = ""
		if session.status == STATUS_CONNECTED:
			session.last_error = ""
			session.restart_attempt_count = 0
			session.consecutive_disconnect_count = 0
	elif event == "session.reconnect_loop":
		# Informational — the self-healer manages restarts. Surface it so the
		# operator can see the gateway is struggling to come back.
		session.last_error = (
			f"Gateway reconnect loop: attempt {data.get('attempts')}, "
			f"next retry in {round((data.get('nextDelayMs') or 0) / 1000)}s"
		)
	else:
		return

	session.last_state_change = frappe.utils.now()
	session.save(ignore_permissions=True)

	from frappe_whatsapp_openwa.utils.cache import invalidate_session_status

	invalidate_session_status(session.name)

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


def _fetch_qr_from_gateway(gateway_session_id: str) -> str:
	try:
		client = get_gateway_client(timeout=5.0)
	except Exception:
		return ""
	return fetch_qr_image(client, gateway_session_id)
