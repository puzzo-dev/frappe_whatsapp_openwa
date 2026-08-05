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
]


def get_gateway_client(timeout: float = 10.0) -> httpx.Client:
	"""Build an httpx client for the configured gateway.

	Auth is sent as X-API-Key (the gateway's documented header); Authorization
	is included as well since the gateway also accepts Bearer tokens.
	"""
	settings = frappe.get_single("OpenWA Gateway Settings")
	if not settings.gateway_base_url:
		frappe.throw(
			frappe._("Configure the Gateway Base URL in OpenWA Gateway Settings first."),
			title=frappe._("Gateway Not Configured"),
		)
	api_key = settings.get_password("gateway_api_key") or ""
	return httpx.Client(
		base_url=settings.gateway_base_url.rstrip("/"),
		headers={
			"X-API-Key": api_key,
			"Authorization": f"Bearer {api_key}",
		},
		timeout=timeout,
	)


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
