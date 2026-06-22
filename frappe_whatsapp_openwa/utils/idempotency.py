"""Redis-backed idempotency guard for inbound webhooks.

Key: openwa:webhook:dedup:{event_type}:{event_id}
TTL: 86400 s (24 h)
"""

from __future__ import annotations

import frappe

_TTL = 86_400
_PREFIX = "openwa:webhook:dedup"


def is_duplicate(event_type: str, event_id: str) -> bool:
	"""Return True if this (event_type, event_id) was already processed."""
	if not event_id:
		return False
	key = f"{_PREFIX}:{event_type}:{event_id}"
	return bool(frappe.cache().exists(key))


def mark_processed(event_type: str, event_id: str) -> None:
	if not event_id:
		return
	key = f"{_PREFIX}:{event_type}:{event_id}"
	frappe.cache().setex(key, _TTL, "1")
