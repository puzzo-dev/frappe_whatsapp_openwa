"""Redis helpers for session status pre-flight checks."""

from __future__ import annotations

import frappe


def get_cached_session_status(session_name: str) -> str | None:
	"""Return the last-polled status from Redis, or None on cache miss."""
	return frappe.cache.get_value(f"openwa:session:status:{session_name}")


def set_cached_session_status(session_name: str, status: str, ttl: int = 60) -> None:
	frappe.cache.set_value(
		f"openwa:session:status:{session_name}", status, expires_in_sec=ttl
	)


def invalidate_session_status(session_name: str) -> None:
	frappe.cache.delete_value(f"openwa:session:status:{session_name}")


def is_session_alive(session_name: str) -> bool:
	"""Fast pre-flight: check Redis first, fall back to live ping on cache miss."""
	cached = get_cached_session_status(session_name)
	if cached is not None:
		return cached == "Connected"
	return _live_ping(session_name)


def _live_ping(session_name: str) -> bool:
	try:
		doc = frappe.get_doc("OpenWA Session", session_name)
		if not doc.gateway_session_id:
			return False
		settings = frappe.get_single("OpenWA Gateway Settings")
		from frappe_whatsapp_openwa.utils.gateway import (
			fetch_session,
			get_gateway_client,
			map_gateway_status,
		)

		client = get_gateway_client(timeout=3.0)
		data = fetch_session(client, doc.gateway_session_id)
		if data is None:
			return False
		status = map_gateway_status(data.get("status"))
		ttl = settings.session_health_ttl_seconds or 60
		set_cached_session_status(session_name, status, ttl)
		return status == "Connected"
	except Exception:
		return False
