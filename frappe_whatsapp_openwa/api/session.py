import frappe

from frappe_whatsapp_openwa.utils.gateway import (
	STATUS_QR_REQUIRED,
	fetch_qr_image,
	fetch_session,
	get_gateway_client,
	map_gateway_status,
)


@frappe.whitelist()
def get_qr(session_name):
	"""Fetch the current QR image from the gateway and persist it on the doc.

	Called by the form poll while status is QR Required — QR codes rotate every
	~20 seconds, so the image must be refreshed from the gateway, not served
	from a stale doc field.
	"""
	doc = frappe.get_doc("OpenWA Session", session_name)
	doc.check_permission("read")
	_sync_from_gateway(doc, want_qr=True)
	return {"qr_code_data": doc.qr_code_data, "status": doc.status}


@frappe.whitelist()
def get_status(session_name):
	"""Fetch the live session status from the gateway and persist it."""
	doc = frappe.get_doc("OpenWA Session", session_name)
	doc.check_permission("read")
	_sync_from_gateway(doc, want_qr=True)
	return {
		"status": doc.status,
		"qr_code_data": doc.qr_code_data if doc.status == STATUS_QR_REQUIRED else "",
		"requires_human_attention": doc.requires_human_attention,
		"last_health_check": doc.last_health_check,
	}


def _sync_from_gateway(doc, want_qr: bool = False) -> None:
	"""Refresh status (and optionally the QR image) from the gateway.

	Silent no-op when the session is not provisioned or the gateway is
	unreachable — the caller always gets a response, possibly with the
	last-known state.
	"""
	if not doc.gateway_session_id:
		return

	try:
		client = get_gateway_client(timeout=5.0)
	except Exception:
		return  # settings not configured

	remote = fetch_session(client, doc.gateway_session_id)
	now = frappe.utils.now()

	if remote is None:
		doc.db_set("last_health_check", now, update_modified=False)
		return

	new_status = map_gateway_status(remote.get("status"))
	qr = ""
	if want_qr and new_status == STATUS_QR_REQUIRED:
		qr = fetch_qr_image(client, doc.gateway_session_id)

	changed = new_status != doc.status
	doc.status = new_status
	doc.last_health_check = now
	doc.last_error = remote.get("lastError") or ""
	if qr:
		doc.qr_code_data = qr
	elif new_status != STATUS_QR_REQUIRED:
		doc.qr_code_data = ""
	if new_status == "Connected":
		doc.restart_attempt_count = 0
		doc.consecutive_disconnect_count = 0
	if changed:
		doc.last_state_change = now
	doc.save(ignore_permissions=True)

	if changed:
		frappe.publish_realtime(
			"openwa_session_state_change",
			{
				"session_name": doc.name,
				"status": doc.status,
				"qr_code_data": doc.qr_code_data or "",
				"requires_human_attention": doc.requires_human_attention,
			},
			after_commit=True,
		)
