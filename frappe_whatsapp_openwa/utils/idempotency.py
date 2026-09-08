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


def claim_event(event_type: str, event_id: str, ttl: int = _TTL) -> bool:
	"""Atomically claim this event for processing.

	Uses Redis SET NX EX so the existence check and the write are a single
	atomic command — no window exists for two concurrent callers to both
	observe a missing key and both proceed.

	Returns True  — this call claimed the event; caller should process it.
	Returns False — already claimed by a prior call; caller should skip (duplicate).

	When event_id is empty, always returns True (no deduplication possible
	without an ID — caller must handle at-least-once delivery itself).

	ttl bounds how long the claim is remembered. Callers keying on a natural
	gateway id (a message id) want the full 24 h. Callers keying on the request
	signature — the only identifier session events have — want a short window,
	because two *legitimate* identical events are byte-identical and would
	otherwise be suppressed for a day.
	"""
	if not event_id:
		return True
	# make_key prefixes the site's db_name — raw SET has no wrapper equivalent
	# for the atomic NX EX combination, and without the prefix keys leak
	# across tenants on a multi-site bench.
	key = frappe.cache.make_key(f"{_PREFIX}:{event_type}:{event_id}")
	# SET NX EX: returns True if the key was set, None if it already existed.
	result = frappe.cache.set(key, "1", nx=True, ex=ttl)
	return result is not None


def release_event(event_type: str, event_id: str) -> None:
	"""Give up a claim so the same event can be processed again.

	Claiming happens before the work, so that concurrent deliveries of one
	occurrence collapse. If that work then fails, the claim has to go: the
	gateway retries a failed delivery with the *same* idempotency key, and a
	claim left behind would dismiss the retry as a duplicate — turning a
	recoverable error into a silently lost event.
	"""
	if not event_id:
		return
	key = frappe.cache.make_key(f"{_PREFIX}:{event_type}:{event_id}")
	try:
		frappe.cache.delete_value(key)
	except Exception:
		# Worst case the claim expires on its own TTL; never break the caller.
		pass
