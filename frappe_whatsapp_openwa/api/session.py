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


@frappe.whitelist()
def request_pairing_code(session_name, phone_number=None):
	"""Request a pairing code for phone-number linking.

	Gateway contract: ``POST /api/sessions/:id/pairing-code`` with body
	``{"phoneNumber": "digits-only"}`` → ``201 {pairingCode, status}``.

	The phone number is normalised to digits-only (6–15 digits). If
	``phone_number`` is not passed, the session's own ``phone_number`` field
	is used.

	Returns ``{"pairing_code": "ABCD1234", "instructions": "..."}`` on
	success. Gateway errors are surfaced verbatim:
	- ``400``: session not started (offer auto-start) / already authenticated / invalid number
	- ``404``: session unknown on gateway
	"""
	from frappe_whatsapp_openwa.utils.phone import to_pairing_code_digits

	doc = frappe.get_doc("OpenWA Session", session_name)
	doc.check_permission("write")

	if not doc.gateway_session_id:
		frappe.throw(
			frappe._("This session is not provisioned on the gateway yet. "
			         "Wait for provisioning to complete or use the manual provision button."),
			title=frappe._("Not Provisioned"),
		)

	if doc.status == "Connected":
		frappe.throw(
			frappe._("This session is already connected. No pairing code needed."),
			title=frappe._("Already Connected"),
		)

	# Use the provided phone or fall back to the session's phone_number.
	phone = phone_number or doc.phone_number
	if not phone:
		frappe.throw(
			frappe._("No phone number provided and the session has none. "
			         "Enter a phone number to request a pairing code."),
			title=frappe._("Phone Required"),
		)

	try:
		digits = to_pairing_code_digits(phone)
	except ValueError as e:
		frappe.throw(str(e), title=frappe._("Invalid Phone Number"))

	_check_pairing_code_rate_limit(session_name)

	client = get_gateway_client(timeout=30.0)

	try:
		resp = client.post(
			f"/api/sessions/{doc.gateway_session_id}/pairing-code",
			json={"phoneNumber": digits},
		)
	except Exception as e:
		frappe.throw(
			frappe._("Gateway request failed: {0}").format(str(e)),
			title=frappe._("Gateway Error"),
		)

	status_code = getattr(resp, "status_code", 0)

	if status_code == 404:
		frappe.throw(
			frappe._("The gateway does not know this session. It may have been deleted. "
			         "Try deprovisioning and re-creating the session."),
			title=frappe._("Session Not Found"),
		)

	if status_code == 400:
		body = {}
		try:
			body = resp.json()
		except Exception:
			pass
		message = body.get("message") or body.get("error") or "Bad request"
		frappe.throw(
			frappe._("Gateway rejected the pairing code request: {0}").format(message),
			title=frappe._("Pairing Code Rejected"),
		)

	if status_code != 201 and status_code != 200:
		frappe.throw(
			frappe._("Unexpected gateway response ({0}).").format(status_code),
			title=frappe._("Gateway Error"),
		)

	body = resp.json()
	pairing_code = body.get("pairingCode") or body.get("pairing_code") or ""

	instructions = (
		"On your phone, open WhatsApp → Settings → Linked Devices → "
		"Link with phone number → enter this code."
	)

	return {
		"pairing_code": pairing_code,
		"instructions": instructions,
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

	# Work out the target state first, then save only if something actually
	# differs. The desk form polls this every 8 seconds while a session is
	# initialising or showing a QR, and an unconditional doc.save() turned each
	# poll into a write plus the full document lifecycle — validate, before_save,
	# on_update, the single-default SQL and every wildcard doc_event — for a
	# session whose state had not moved. Ten open forms did ~75 no-op writes a
	# minute.
	#
	# The comparison covers every field this function assigns, not just status:
	# last_error and the retry counters change on their own, and gating on
	# status alone would silently stop persisting them.
	target = {
		"status": new_status,
		"last_error": remote.get("lastError") or "",
	}
	if qr:
		target["qr_code_data"] = qr
	elif new_status != STATUS_QR_REQUIRED:
		target["qr_code_data"] = ""
	if new_status == "Connected":
		target["restart_attempt_count"] = 0
		target["consecutive_disconnect_count"] = 0

	needs_save = any(
		(doc.get(field) or None) != (value or None) for field, value in target.items()
	)

	if not needs_save:
		# Nothing moved. Record that the poll happened without touching the
		# document lifecycle, and leave `modified` alone so open forms do not
		# see a spurious "document was modified" conflict.
		doc.db_set("last_health_check", now, update_modified=False)
		return

	for field, value in target.items():
		setattr(doc, field, value)
	doc.last_health_check = now
	if changed:
		doc.last_state_change = now
	# Sync path — bypass the manual-edit guard.
	frappe.flags.openwa_sync = True
	try:
		doc.save(ignore_permissions=True)
	finally:
		frappe.flags.openwa_sync = False

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


# WhatsApp itself throttles device-linking attempts and can block a number that
# requests too many, so the cost of an unbounded loop here is not just gateway
# load — it is the phone number becoming unlinkable. A human clicking the button
# needs a handful of attempts; anything past that is a script.
_PAIRING_CODE_MAX_REQUESTS = 5
_PAIRING_CODE_WINDOW_SECONDS = 600


def _check_pairing_code_rate_limit(session_name: str) -> None:
	"""Throw once a session exceeds the pairing-code request budget."""
	# make_key prefixes the site's db_name so the counter is tenant-scoped.
	key = frappe.cache.make_key(f"openwa:pairing_code:{session_name}")
	pipe = frappe.cache.pipeline()
	pipe.incr(key)
	pipe.ttl(key)
	count, ttl = pipe.execute()
	if ttl < 0:
		# First call in this window, or the expiry was lost — (re)apply it.
		frappe.cache.expire(key, _PAIRING_CODE_WINDOW_SECONDS)
	if count > _PAIRING_CODE_MAX_REQUESTS:
		frappe.throw(
			frappe._("Too many pairing code requests for this session. "
			         "Wait a few minutes before trying again."),
			frappe.TooManyRequestsError,
			title=frappe._("Rate Limited"),
		)
