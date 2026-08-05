"""Redis-backed idempotency guard for inbound webhooks.

Key: openwa:webhook:dedup:{event_type}:{event_id}
TTL: 86400 s (24 h)

Single atomic operation via SET NX EX — eliminates the TOCTOU race that exists
when is_duplicate() and mark_processed() are called as two separate operations.
"""

from __future__ import annotations

import frappe

_TTL = 86_400
_PREFIX = "openwa:webhook:dedup"


def claim_event(event_type: str, event_id: str) -> bool:
	"""Atomically claim this event for processing.

	Uses Redis SET NX EX so the existence check and the write are a single
	atomic command — no window exists for two concurrent callers to both
	observe a missing key and both proceed.

	Returns True  — this call claimed the event; caller should process it.
	Returns False — already claimed by a prior call; caller should skip (duplicate).

	When event_id is empty, always returns True (no deduplication possible
	without an ID — caller must handle at-least-once delivery itself).
	"""
	if not event_id:
		return True
	# make_key prefixes the site's db_name — raw SET has no wrapper equivalent
	# for the atomic NX EX combination, and without the prefix keys leak
	# across tenants on a multi-site bench.
	key = frappe.cache.make_key(f"{_PREFIX}:{event_type}:{event_id}")
	# SET NX EX: returns True if the key was set, None if it already existed.
	result = frappe.cache.set(key, "1", nx=True, ex=_TTL)
	return result is not None
