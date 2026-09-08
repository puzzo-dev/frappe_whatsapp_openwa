"""Shared helpers for talking to the OpenWA gateway (rmyndharis/OpenWA).

Centralises:
- httpx client construction (base URL + API key auth headers)
- gateway → desk status mapping
- QR code retrieval
- gateway-compliant session name building
"""

from __future__ import annotations

import re

import frappe
import httpx

from frappe_whatsapp_openwa.providers.base import GATEWAY_STATUS_MAP

# Desk-level statuses shown on the OpenWA Session doctype.
STATUS_INITIALIZING = "Initializing"
STATUS_QR_REQUIRED = "QR Required"
STATUS_CONNECTED = "Connected"
STATUS_DISCONNECTED = "Disconnected"
STATUS_FAILED = "Failed"

# Events we subscribe the session webhook to on the gateway.
WEBHOOK_EVENTS = [
	"message.received",
	"message.ack",
	"message.failed",
	"session.qr",
	"session.authenticated",
	"session.disconnected",
	"session.status",
	# handle_session_event has always had a branch for this one; without it in
	# the subscription the gateway never sent it and the branch was unreachable.
	"session.reconnect_loop",
]


# Clients are reused rather than rebuilt per call. Every call was opening a new
# httpx.Client and never closing it, so each gateway request paid a fresh TCP
# (and TLS) handshake and leaked a connection pool until garbage collection —
# on the health poll that is one per session per five minutes, forever.
#
# The key includes the site, the base URL and the API key, so a multi-tenant
# bench can never hand one site's client — and therefore its credentials — to
# another. Timeout is part of the key too: callers pass a timeout that suits
# their operation (3s for a health check, 35s for a restart) and httpx fixes it
# at construction, so keying on it keeps that behaviour exactly while still
# collapsing the repeated calls at each timeout down to one client.
_CLIENTS: dict[tuple, httpx.Client] = {}


# The send path wants a tighter, split timeout than a health poll: connect fast,
# give the gateway a few seconds to answer, and never queue behind the pool for
# long. Expressed as a tuple so it stays hashable and can be part of the pool key.
SEND_TIMEOUT = (2.0, 5.0, 5.0, 2.0)


def get_gateway_client(timeout: float | tuple = 10.0) -> httpx.Client:
	"""Return a pooled httpx client for the configured gateway.

	Auth is sent as X-API-Key (the gateway's documented header); Authorization
	is included as well since the gateway also accepts Bearer tokens.

	`timeout` is either a single number or a (connect, read, write, pool) tuple
	for callers that want them set separately — SEND_TIMEOUT is the one the
	message path uses.

	The client is shared, so callers must not close it. httpx.Client is safe to
	use from several threads.
	"""
	settings = frappe.get_single("OpenWA Gateway Settings")
	if not settings.gateway_base_url:
		frappe.throw(
			frappe._("Configure the Gateway Base URL in OpenWA Gateway Settings first."),
			title=frappe._("Gateway Not Configured"),
		)
	api_key = settings.get_password("gateway_api_key") or ""
	base_url = settings.gateway_base_url.rstrip("/")

	key = (getattr(frappe.local, "site", None), base_url, api_key, timeout)
	client = _CLIENTS.get(key)
	if client is not None and not client.is_closed:
		return client

	client = httpx.Client(
		base_url=base_url,
		headers={
			"X-API-Key": api_key,
			"Authorization": f"Bearer {api_key}",
		},
		timeout=(
			httpx.Timeout(connect=timeout[0], read=timeout[1], write=timeout[2], pool=timeout[3])
			if isinstance(timeout, tuple)
			else timeout
		),
	)
	# Rotating the URL or key produces a new key, so the stale client would sit
	# here holding sockets open. Only one configuration is ever live per site.
	for stale_key in [k for k in _CLIENTS if k[0] == key[0] and k != key and k[1:3] != key[1:3]]:
		try:
			_CLIENTS.pop(stale_key).close()
		except Exception:
			pass
	_CLIENTS[key] = client
	return client


def map_gateway_status(gateway_status: str | None) -> str:
	"""Translate a gateway status (e.g. 'qr_ready') into a desk status."""
	return GATEWAY_STATUS_MAP.get((gateway_status or "").lower(), STATUS_FAILED)


def build_gateway_session_name(doc) -> str:
	"""Build a gateway-compliant session name.

	The gateway requires 3–50 chars of letters/numbers/hyphens, unique per
	deployment. We namespace with the site so multiple Frappe sites sharing
	one gateway never collide.
	"""
	tenant = re.sub(r"[^a-zA-Z0-9]+", "-", frappe.local.site).strip("-").lower()
	slug = re.sub(r"[^a-zA-Z0-9]+", "-", doc.session_name or doc.name).strip("-").lower()
	name = f"{tenant}-{slug}"
	if len(name) > 50:
		name = name[:50].rstrip("-")
	if len(name) < 3:
		name = f"{name}-wa"
	return name


def fetch_session(client: httpx.Client, gateway_session_id: str) -> dict | None:
	"""GET /api/sessions/:id — returns the session dict or None on failure."""
	try:
		resp = client.get(f"/api/sessions/{gateway_session_id}")
		if resp.status_code == 200:
			return resp.json()
	except Exception:
		pass
	return None


def fetch_qr_image(client: httpx.Client, gateway_session_id: str) -> str:
	"""GET /api/sessions/:id/qr — returns the data-URL PNG, or "" if not ready.

	The gateway returns 400 while the QR is not ready (or the session is
	already authenticated), both of which are normal transient states.
	"""
	try:
		resp = client.get(f"/api/sessions/{gateway_session_id}/qr")
		if resp.status_code == 200:
			data = resp.json()
			return data.get("qrCode") or ""
	except Exception:
		pass
	return ""


def error_message_from_response(resp: httpx.Response) -> str:
	"""Extract a human-readable error from a gateway error response."""
	try:
		body = resp.json()
		msg = body.get("message") or body.get("error") or ""
		if isinstance(msg, list):
			msg = "; ".join(str(m) for m in msg)
		return str(msg)[:300]
	except Exception:
		return resp.text[:300] if resp.text else ""
