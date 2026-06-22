"""Redis helpers for session status pre-flight checks."""

from __future__ import annotations

import frappe


def get_cached_session_status(session_name: str) -> str | None:
	"""Return the last-polled status from Redis, or None on cache miss."""
	raw = frappe.cache().get(f"openwa:session:status:{session_name}")
	if raw:
		return raw.decode() if isinstance(raw, bytes) else raw
	return None


def set_cached_session_status(session_name: str, status: str, ttl: int = 60) -> None:
	frappe.cache().setex(f"openwa:session:status:{session_name}", ttl, status)


def invalidate_session_status(session_name: str) -> None:
	frappe.cache().delete(f"openwa:session:status:{session_name}")


def is_session_alive(session_name: str) -> bool:
	"""Fast pre-flight: check Redis first, fall back to live ping on cache miss."""
	cached = get_cached_session_status(session_name)
	if cached is not None:
		return cached == "Connected"
	return _live_ping(session_name)


def _live_ping(session_name: str) -> bool:
	try:
		doc = frappe.get_doc("OpenWA Session", session_name)
		settings = frappe.get_single("OpenWA Gateway Settings")
		import httpx

		client = httpx.Client(
			base_url=settings.gateway_base_url,
			headers={"Authorization": f"Bearer {settings.get_password('gateway_api_key')}"},
			timeout=3.0,
		)
		resp = client.get(f"/api/sessions/{doc.gateway_session_id}/status")
		data = resp.json()
		status = data.get("status", "Unknown")
		ttl = settings.session_health_ttl_seconds or 60
		set_cached_session_status(session_name, status, ttl)
		return status == "Connected"
	except Exception:
		return False
