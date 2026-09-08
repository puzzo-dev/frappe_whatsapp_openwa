"""One atomic sliding-window limiter, shared by the Meta and OpenWA limiters.

Both were written the same way and both were wrong the same way: trim the
window, count it, then decide, then add — as separate round trips. Two workers
can each read a count below the ceiling and each add, so the window goes over
its limit exactly when load is high enough for the limit to matter.

The Meta limiter had a second bug on top: it added its entry *before* looking
at the count, so a call that was refused still spent a slot. A caller that keeps
retrying then holds its own window full and never recovers — the limiter stops
being a ceiling and becomes a trap.

Doing it in Lua makes trim, count and add one operation as far as every other
client is concerned, which is the only way the check and the write cannot be
separated. Members are random rather than the timestamp: two calls in the same
microsecond would otherwise write the same member, and the second would replace
the first instead of counting as its own call.
"""

from __future__ import annotations

import uuid

import frappe

# KEYS[1] window   ARGV: now, window_seconds, allowance, member
_TAKE_SLOT = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local allowance = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local used = redis.call('ZCARD', KEYS[1])
if used >= allowance then
  return {0, used}
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('EXPIRE', KEYS[1], window)
return {1, used + 1}
"""

# KEYS[1] window   ARGV: now, window_seconds
_PEEK = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
return redis.call('ZCARD', KEYS[1])
"""


def take_slot(key: str, allowance: int, window_seconds: int) -> tuple[bool, int]:
	"""Claim one slot in the window. Returns (granted, calls_in_window).

	`key` is the unprefixed name; the site prefix is applied here so every
	caller is tenant-scoped without having to remember.

	A non-positive allowance means unlimited, and nothing is recorded — there is
	no ceiling to measure against, so the entries would only cost memory.
	"""
	if allowance <= 0:
		return True, 0

	now = frappe.utils.now_datetime().timestamp()
	granted, used = frappe.cache.eval(
		_TAKE_SLOT,
		1,
		frappe.cache.make_key(key),
		now,
		window_seconds,
		allowance,
		uuid.uuid4().hex,
	)
	return bool(granted), int(used)


def give_back(key: str) -> bool:
	"""Return the newest slot in the window. True if one was there to return.

	Which entry is removed does not matter: they are interchangeable for
	counting, and only their timestamps decide when they age out. Popping the
	newest is the closest thing to undoing the take that just happened, and it
	needs no token threaded back through the callers.
	"""
	try:
		return bool(frappe.cache.zpopmax(frappe.cache.make_key(key), 1))
	except Exception:
		# A slot that cannot be handed back ages out of the window on its own.
		return False


def calls_in_window(key: str, window_seconds: int) -> int:
	"""How full the window is right now, without taking anything from it."""
	now = frappe.utils.now_datetime().timestamp()
	return int(
		frappe.cache.eval(_PEEK, 1, frappe.cache.make_key(key), now, window_seconds)
	)
