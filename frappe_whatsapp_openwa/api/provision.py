"""Session provisioning — registers a session with the OpenWA gateway.

Flow (gateway contract: rmyndharis/OpenWA):
  1. Operator creates an OpenWA Session doc in Frappe (status=Initializing)
  2. after_insert queues provision, or the operator clicks
     "Create Session on Gateway" → provision_session()
  3. POST /api/sessions {name} → gateway returns its own session UUID
  4. POST /api/sessions/:id/webhooks → register our inbound webhook endpoint
  5. POST /api/sessions/:id/start → engine boots and a pairing QR is generated
  6. GET /api/sessions/:id/qr → QR image stored on the doc (status=QR Required)
  7. Operator scans the QR on the form page
  8. session.authenticated webhook → status goes to Connected
"""

from __future__ import annotations

import frappe

# Per-user rate limit: max 10 provision/deprovision calls per hour.
_PROVISION_RATE_LIMIT_MAX = 10
_PROVISION_RATE_LIMIT_WINDOW = 3600  # seconds


def _check_provision_rate_limit() -> None:
	"""Prevent rapid provisioning abuse by authenticated users."""
	user = frappe.session.user or "Guest"
	key = frappe.cache.make_key(f"openwa:provision:ratelimit:{frappe.scrub(user)}")
	pipe = frappe.cache.pipeline()
	pipe.incr(key)
	pipe.ttl(key)
	count, ttl = pipe.execute()
	if ttl < 0:
		frappe.cache.expire(key, _PROVISION_RATE_LIMIT_WINDOW)
	if count > _PROVISION_RATE_LIMIT_MAX:
		frappe.throw(
			frappe._("Too many provisioning requests. Please wait before trying again."),
			frappe.TooManyRequestsError,
		)


@frappe.whitelist()
def provision_session(session_name: str) -> dict:
	"""Register an OpenWA Session on the gateway and start its engine.

	Returns {"gateway_session_id": "...", "status": "...", "qr_code_data": "..."}.
	Raises on failure.
	"""
	_check_provision_rate_limit()
	doc = frappe.get_doc("OpenWA Session", session_name)
	doc.check_permission("write")
	return _do_provision(doc)


def provision_session_async(session_name: str) -> None:
	"""Background-job entry point — auto-provision after the doc is inserted."""
	try:
		doc = frappe.get_doc("OpenWA Session", session_name)
		if doc.gateway_session_id:
			return
		_do_provision(doc)
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title=f"OpenWA auto-provision failed for {session_name}",
			message=frappe.get_traceback(),
		)


def _do_provision(doc) -> dict:
	if doc.gateway_session_id:
		frappe.throw(
			frappe._(f"Session is already registered on the gateway ({doc.gateway_session_id})."),
			title=frappe._("Already Provisioned"),
		)

	import httpx

	from frappe_whatsapp_openwa.utils.gateway import (
		STATUS_QR_REQUIRED,
		build_gateway_session_name,
		fetch_qr_image,
		fetch_session,
		get_gateway_client,
		map_gateway_status,
	)

	client = get_gateway_client(timeout=15.0)
	gateway_name = build_gateway_session_name(doc)

	try:
		session_id = _create_or_find_session(client, gateway_name)
		_register_webhook(client, session_id)
		_start_session(client, session_id)
	except httpx.RequestError as e:
		frappe.throw(
			frappe._(f"Could not reach OpenWA gateway: {e}"),
			title=frappe._("Network Error"),
		)
		return {}

	# The engine boots asynchronously — the QR may not be ready yet. Whatever
	# state the gateway reports now is stored; the health poll and webhooks
	# keep it fresh from here.
	remote = fetch_session(client, session_id) or {}
	status = map_gateway_status(remote.get("status"))
	qr = fetch_qr_image(client, session_id) if status == STATUS_QR_REQUIRED else ""

	doc.gateway_session_id = session_id
	doc.status = status
	doc.qr_code_data = qr
	doc.last_error = remote.get("lastError") or ""
	doc.last_state_change = frappe.utils.now()
	doc.save(ignore_permissions=True)

	_publish_state(doc)

	return {
		"gateway_session_id": doc.gateway_session_id,
		"status": doc.status,
		"qr_code_data": qr,
	}


def _create_or_find_session(client, gateway_name: str) -> str:
	"""POST /api/sessions — returns the gateway's session UUID.

	On 409 (name already exists) the existing session is looked up by name and
	reused, so retrying a failed provision never strands the doc.
	"""
	from frappe_whatsapp_openwa.utils.gateway import error_message_from_response

	resp = client.post("/api/sessions", json={"name": gateway_name})
	if resp.status_code == 201:
		return resp.json()["id"]
	if resp.status_code == 409:
		existing = _find_session_by_name(client, gateway_name)
		if existing:
			return existing
		frappe.throw(
			frappe._(
				f"The gateway already has a session named '{gateway_name}' but it could not be "
				"looked up. Delete it on the gateway or rename this session."
			),
			title=frappe._("Name Conflict"),
		)
	frappe.throw(
		frappe._(f"Gateway returned HTTP {resp.status_code}: {error_message_from_response(resp)}"),
		title=frappe._("Provisioning Failed"),
	)
	return ""


def _find_session_by_name(client, gateway_name: str) -> str | None:
	resp = client.get("/api/sessions")
	if resp.status_code != 200:
		return None
	for session in resp.json():
		if session.get("name") == gateway_name:
			return session.get("id")
	return None


def _register_webhook(client, session_id: str) -> None:
	"""Point the gateway's webhook for this session at our inbound endpoint."""
	from frappe_whatsapp_openwa.utils.gateway import WEBHOOK_EVENTS

	payload = {"url": _webhook_url(), "events": WEBHOOK_EVENTS}
	secret = (frappe.get_single("OpenWA Gateway Settings").get_password("webhook_secret") or "").strip()
	if secret:
		payload["secret"] = secret

	resp = client.post(f"/api/sessions/{session_id}/webhooks", json=payload)
	if resp.status_code not in (200, 201, 409):
		# Non-fatal: QR linking and outbound sending still work, and the 5-minute
		# health poll keeps statuses fresh — but inbound messages need this.
		frappe.log_error(
			title=f"OpenWA webhook registration failed for {session_id}",
			message=f"HTTP {resp.status_code}: {resp.text[:500]}",
		)


def _start_session(client, session_id: str) -> None:
	"""POST /api/sessions/:id/start — boots the engine so a QR is generated.

	400 means the engine is already running (fine); 409 means a credential
	teardown is still in flight (retryable).
	"""
	from frappe_whatsapp_openwa.utils.gateway import error_message_from_response

	resp = client.post(f"/api/sessions/{session_id}/start", timeout=35.0)
	if resp.status_code == 200:
		return
	if resp.status_code == 400:
		return  # already started
	if resp.status_code == 409:
		frappe.throw(
			frappe._("The gateway is still tearing down a previous run of this session. Try again in a few seconds."),
			title=frappe._("Session Busy"),
		)
	frappe.throw(
		frappe._(f"Could not start the session on the gateway (HTTP {resp.status_code}): {error_message_from_response(resp)}"),
		title=frappe._("Start Failed"),
	)


def _publish_state(doc) -> None:
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


@frappe.whitelist()
def deprovision_session(session_name: str) -> dict:
	"""Remove an OpenWA Session from the gateway and reset this doc."""
	_check_provision_rate_limit()
	doc = frappe.get_doc("OpenWA Session", session_name)
	doc.check_permission("write")

	if not doc.gateway_session_id:
		return {"status": "nothing_to_delete"}

	from frappe_whatsapp_openwa.utils.gateway import get_gateway_client

	client = get_gateway_client(timeout=10.0)
	try:
		client.delete(f"/api/sessions/{doc.gateway_session_id}")
	except Exception:
		frappe.log_error(
			title=f"OpenWA: gateway DELETE failed for {doc.gateway_session_id}",
			message=frappe.get_traceback(),
		)
		doc.requires_human_attention = 1

	doc.gateway_session_id = ""
	doc.status = "Initializing"
	doc.qr_code_data = ""
	doc.last_error = ""
	doc.restart_attempt_count = 0
	doc.consecutive_disconnect_count = 0
	doc.save(ignore_permissions=True)

	from frappe_whatsapp_openwa.utils.cache import invalidate_session_status
	invalidate_session_status(doc.name)

	return {"status": "deprovisioned"}


def _webhook_url() -> str:
	base = frappe.utils.get_url()
	return f"{base}/api/method/frappe_whatsapp_openwa.api.webhook.receive"
