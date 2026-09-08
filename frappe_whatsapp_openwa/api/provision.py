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

from frappe_whatsapp_openwa.utils.settings import limit

# Per-user rate limit: max 10 provision/deprovision calls per hour.


def _check_provision_rate_limit() -> None:
	"""Prevent rapid provisioning abuse by authenticated users."""
	user = frappe.session.user or "Guest"
	key = frappe.cache.make_key(f"openwa:provision:ratelimit:{frappe.scrub(user)}")
	pipe = frappe.cache.pipeline()
	pipe.incr(key)
	pipe.ttl(key)
	count, ttl = pipe.execute()
	if ttl < 0:
		frappe.cache.expire(key, limit("provision_rate_limit_window_seconds", 3600))
	if count > limit("provision_rate_limit_max", 10):
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
	"""Background-job entry point — auto-provision after the doc is inserted.

	On failure, sets ``last_error`` and ``requires_human_attention`` on the
	session doc so the operator sees *why* no QR appeared, right on the form
	— not just in Error Log.
	"""
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
		# Surface the failure on the doc itself.
		try:
			frappe.flags.openwa_sync = True
			frappe.db.set_value(
				"OpenWA Session",
				session_name,
				{
					"last_error": str(frappe.get_traceback().split("\n")[-2] if frappe.get_traceback() else "Provisioning failed"),
					"requires_human_attention": 1,
				},
				update_modified=False,
			)
			# Committed, or it goes back with the transaction the exception is
			# unwinding — which is the whole point of writing it. The operator
			# was meant to see why no QR appeared, on the form; without this the
			# field was set and immediately rolled back, and the only trace left
			# was the Error Log this was written to improve on.
			frappe.db.commit()
		finally:
			frappe.flags.openwa_sync = False


def _do_provision(doc) -> dict:
	# Every route into the gateway passes through here, so the budget is checked
	# here rather than only on the whitelisted endpoints. The background path had
	# no limit at all: inserting sessions in bulk enqueued one unthrottled
	# provision each, and every provision is five calls to the gateway (create,
	# register webhook, start, fetch status, fetch QR). A loop or an import could
	# put the dashboard under real load.
	_check_provision_rate_limit()

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

		# Record the gateway's id the moment it exists, before anything else can
		# fail. It used to be written only after the webhook, the start call and
		# two status fetches had all succeeded — so any error in between left a
		# live session on the gateway that this doc had no reference to. The
		# operator sees an unprovisioned session, deletes it, creates another,
		# and the orphan stays running: sessions accumulate on the gateway with
		# nothing pointing at them.
		#
		# db_set rather than save(): it writes the one column without the
		# document lifecycle, so it cannot itself fail on validation and it
		# survives the exception path below.
		if session_id and doc.gateway_session_id != session_id:
			doc.db_set("gateway_session_id", session_id, update_modified=False)
			frappe.db.commit()

		_register_webhook(client, session_id, session_name=doc.name)
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
	# Sync path — bypass the manual-edit guard in validate().
	frappe.flags.openwa_sync = True
	try:
		doc.save(ignore_permissions=True)
	finally:
		frappe.flags.openwa_sync = False

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


def ensure_session_webhook_secret(session_name: str) -> str:
	"""Return this session's webhook secret, generating one if it has none.

	The gateway registers webhooks per session and signs each delivery with that
	webhook's own secret, so there is no reason for every session to share one.
	A per-session secret means the signature proves which session sent the
	delivery rather than the body merely claiming it, contains a leak to one
	number, and lets a single session be rotated on its own.

	Stored as a Password field, so it is encrypted at rest and never rendered.
	The gateway treats `secret` as write-only too and never reads it back.
	"""
	from frappe.utils.password import get_decrypted_password
	from frappe.utils.password import set_encrypted_password
	secret = None
	try:
		secret = get_decrypted_password(
			"OpenWA Session", session_name, "webhook_secret", raise_exception=False
		)
	except Exception:
		secret = None

	if not secret:
		# The gateway requires at least 16 characters.
		secret = frappe.generate_hash(length=48)
		set_encrypted_password(
			"OpenWA Session", session_name, secret, "webhook_secret"
		)
		frappe.db.commit()

		# The verifier caches secrets by gateway session id; a stale entry would
		# keep verifying against the key this just replaced.
		from frappe_whatsapp_openwa.api.webhook import forget_session_secret

		forget_session_secret(
			frappe.db.get_value("OpenWA Session", session_name, "gateway_session_id")
		)
	return secret


def _register_webhook(client, session_id: str, session_name: str | None = None) -> None:
	"""Point the gateway's webhook for this session at our inbound endpoint."""
	from frappe_whatsapp_openwa.utils.gateway import WEBHOOK_EVENTS

	payload = {"url": _webhook_url(), "events": WEBHOOK_EVENTS}

	# Always this session's own secret — there is no shared one to fall back to.
	secret = ""
	if session_name:
		try:
			secret = ensure_session_webhook_secret(session_name) or ""
		except Exception:
			frappe.log_error(
				title=f"OpenWA: could not prepare webhook secret for {session_name}",
				message=frappe.get_traceback(),
			)
	if secret:
		payload["secret"] = secret

	resp = client.post(f"/api/sessions/{session_id}/webhooks", json=payload)
	if resp.status_code not in (200, 201, 409):
		detail = f"HTTP {resp.status_code}: {resp.text[:300]}"
		frappe.log_error(
			title=f"OpenWA webhook registration failed for {session_id}",
			message=detail,
		)

		# Say so on the session. Outbound sending still works, so provisioning
		# looks successful — but without a webhook nothing reports back, so a
		# scanned QR never turns into Connected and the session appears to hang.
		# Leaving that in the Error Log only is how it goes unnoticed.
		if session_name:
			hint = ""
			if resp.status_code == 400 and "not allowed" in (resp.text or "").lower():
				hint = (
					" The gateway refused this callback address. Set Webhook Callback URL "
					"in OpenWA Gateway Settings to a URL it can reach — a local site needs "
					"a public tunnel."
				)
			frappe.db.set_value(
				"OpenWA Session",
				session_name,
				{
					"last_error": f"Webhook not registered — {detail}.{hint}",
					"requires_human_attention": 1,
				},
				update_modified=False,
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
	# Sync path — bypass the manual-edit guard.
	frappe.flags.openwa_sync = True
	try:
		doc.save(ignore_permissions=True)
	finally:
		frappe.flags.openwa_sync = False

	from frappe_whatsapp_openwa.utils.cache import invalidate_session_status
	invalidate_session_status(doc.name)

	return {"status": "deprovisioned"}


def _webhook_url() -> str:
	"""The address the gateway will POST events to.

	Defaults to this site's own URL, which is right whenever the gateway can
	reach it. It cannot when the site is local and the gateway is remote: the
	gateway runs the callback through an SSRF guard and answers
	400 "Destination address is not allowed" for localhost or a private
	network, so no webhook is registered and nothing ever reports back.

	Webhook Callback URL overrides it, so a local site can point the gateway at
	a public tunnel and be tested against the real thing.
	"""
	configured = (
		frappe.db.get_single_value("OpenWA Gateway Settings", "webhook_callback_url") or ""
	).strip()
	if configured:
		# Given whole, use it whole; given as a host, append the endpoint.
		if "/api/method/" in configured:
			return configured
		return configured.rstrip("/") + "/api/method/frappe_whatsapp_openwa.api.webhook.receive"

	base = frappe.utils.get_url()
	return f"{base}/api/method/frappe_whatsapp_openwa.api.webhook.receive"


def release_gateway_session(session_name: str, gateway_session_id: str) -> None:
	"""Delete a session on the gateway when its Frappe document goes away.

	Without this the gateway keeps the session running forever: deleting the
	document here removed the only reference to it, so nothing could reconnect
	it, reuse it, or clean it up. Each create-and-delete cycle stranded another
	live session on the dashboard.

	Best effort by design — a document delete must not fail because the gateway
	is unreachable. What cannot be deleted now is reported, and stays visible on
	the gateway for an operator to remove.
	"""
	if not gateway_session_id:
		return

	# Tests and fixture teardown delete sessions constantly and have no gateway
	# to talk to; the same flag that skips provisioning skips the release.
	if frappe.flags.get("openwa_skip_provision"):
		return

	# Nothing to release against an unconfigured gateway, and reporting that as
	# a failure on every delete would be noise rather than signal.
	try:
		settings = frappe.get_single("OpenWA Gateway Settings")
		if not (settings.gateway_base_url or "").strip():
			return
	except Exception:
		return

	from frappe_whatsapp_openwa.utils.gateway import get_gateway_client

	try:
		client = get_gateway_client(timeout=10.0)
		resp = client.delete(f"/api/sessions/{gateway_session_id}")
		if resp.status_code not in (200, 202, 204, 404):
			frappe.log_error(
				title=f"OpenWA: could not release gateway session {gateway_session_id}",
				message=(f"Session {session_name} was deleted in Frappe but the gateway "
				         f"answered HTTP {resp.status_code}. The session is still running "
				         f"there and needs removing by hand."),
			)
	except Exception:
		frappe.log_error(
			title=f"OpenWA: could not release gateway session {gateway_session_id}",
			message=frappe.get_traceback(),
		)
