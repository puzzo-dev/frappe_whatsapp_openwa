"""Session provisioning — registers a new session with the shared OpenWA gateway.

Flow:
  1. Operator creates an OpenWA Session doc in Frappe (status=Initializing)
  2. Clicks "Initialize on Gateway" → calls provision_session()
  3. We POST to the gateway; receive gateway_session_id
  4. Gateway pushes session.qr event → webhook → monitoring/state updates QR
  5. Operator scans QR on the form page
  6. Gateway pushes session.connected → status goes to Connected
"""

from __future__ import annotations

import frappe

# Per-user rate limit: max 10 provision/deprovision calls per hour.
_PROVISION_RATE_LIMIT_MAX = 10
_PROVISION_RATE_LIMIT_WINDOW = 3600  # seconds


def _check_provision_rate_limit() -> None:
	"""Prevent rapid provisioning abuse by authenticated users."""
	user = frappe.session.user or "Guest"
	key = f"openwa:provision:ratelimit:{frappe.scrub(user)}"
	pipe = frappe.cache().pipeline()
	pipe.incr(key)
	pipe.ttl(key)
	count, ttl = pipe.execute()
	if ttl < 0:
		frappe.cache().expire(key, _PROVISION_RATE_LIMIT_WINDOW)
	if count > _PROVISION_RATE_LIMIT_MAX:
		frappe.throw(
			frappe._("Too many provisioning requests. Please wait before trying again."),
			frappe.TooManyRequestsError,
		)


@frappe.whitelist()
def provision_session(session_name: str) -> dict:
	"""Register an OpenWA Session on the shared gateway.

	Returns {"gateway_session_id": "...", "status": "Initializing"}.
	Raises frappe.ValidationError on failure.
	"""
	_check_provision_rate_limit()
	doc = frappe.get_doc("OpenWA Session", session_name)
	doc.check_permission("write")

	if doc.gateway_session_id:
		frappe.throw(
			frappe._(f"Session is already registered on the gateway ({doc.gateway_session_id})."),
			title=frappe._("Already Provisioned"),
		)

	settings = frappe.get_single("OpenWA Gateway Settings")
	if not settings.gateway_base_url:
		frappe.throw(
			frappe._("Configure the Gateway Base URL in OpenWA Gateway Settings first."),
			title=frappe._("Gateway Not Configured"),
		)

	import httpx

	session_id = _build_session_id(doc)
	client = httpx.Client(
		base_url=settings.gateway_base_url,
		headers={"Authorization": f"Bearer {settings.get_password('gateway_api_key')}"},
		timeout=10.0,
	)

	try:
		resp = client.post("/api/sessions", json={
			"id": session_id,
			"config": {
				"webhook": _webhook_url(),
				"webHookAllowSelfSigned": True,
			},
		})
		if resp.status_code not in (200, 201, 409):
			try:
				body = resp.json()
				error_detail = str(body.get("message") or body.get("error") or "")[:200]
			except Exception:
				error_detail = ""
			frappe.throw(
				frappe._(f"Gateway returned HTTP {resp.status_code}{': ' + error_detail if error_detail else '.'}"),
				title=frappe._("Provisioning Failed"),
			)
		data = resp.json() if resp.text else {}
	except httpx.RequestError as e:
		frappe.throw(
			frappe._(f"Could not reach OpenWA gateway: {e}"),
			title=frappe._("Network Error"),
		)
		return {}

	doc.gateway_session_id = data.get("id") or session_id
	doc.status = "Initializing"
	doc.last_state_change = frappe.utils.now()
	doc.save(ignore_permissions=True)

	return {
		"gateway_session_id": doc.gateway_session_id,
		"status": doc.status,
	}


@frappe.whitelist()
def deprovision_session(session_name: str) -> dict:
	"""Remove an OpenWA Session from the gateway and reset this doc."""
	_check_provision_rate_limit()
	doc = frappe.get_doc("OpenWA Session", session_name)
	doc.check_permission("write")

	if not doc.gateway_session_id:
		return {"status": "nothing_to_delete"}

	settings = frappe.get_single("OpenWA Gateway Settings")
	import httpx

	client = httpx.Client(
		base_url=settings.gateway_base_url,
		headers={"Authorization": f"Bearer {settings.get_password('gateway_api_key')}"},
		timeout=10.0,
	)
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
	doc.restart_attempt_count = 0
	doc.consecutive_disconnect_count = 0
	doc.save(ignore_permissions=True)

	from frappe_whatsapp_openwa.utils.cache import invalidate_session_status
	invalidate_session_status(doc.name)

	return {"status": "deprovisioned"}


def _build_session_id(doc) -> str:
	"""Build the namespaced session ID: {tenant}-{phone}-{index}.

	The index is extracted from the trailing numeric segment of the session_name
	(e.g. "My Session-003" → 3). Falls back to the raw doc name when no numeric
	suffix exists, ensuring gateway IDs are always unique even for non-standard names.
	"""
	tenant = frappe.local.site.replace(".", "-")
	phone = (doc.phone_number or "").replace("+", "").replace(" ", "").replace("-", "")

	suffix = (doc.session_name or "").rsplit("-", 1)[-1] if "-" in (doc.session_name or "") else ""
	if suffix.isdigit():
		index = int(suffix)
		return f"{tenant}-{phone}-{index}"

	# Non-numeric suffix — use a URL-safe slug of the full name to preserve uniqueness.
	import re
	slug = re.sub(r"[^a-z0-9]+", "-", (doc.session_name or doc.name or "").lower()).strip("-")
	return f"{tenant}-{phone}-{slug}"


def _webhook_url() -> str:
	base = frappe.utils.get_url()
	return f"{base}/api/method/frappe_whatsapp_openwa.api.webhook.receive"
