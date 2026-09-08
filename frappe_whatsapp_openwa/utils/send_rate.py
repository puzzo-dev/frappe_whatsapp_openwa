"""Send-rate limiting for OpenWA — how fast, not how much.

A daily cap and a send rate answer different questions and neither substitutes
for the other. The daily cap bounds total volume for a number; the rate bounds
how quickly that volume goes out. A campaign that respects a 1000/day cap by
firing all 1000 in two minutes is exactly the pattern that gets an unofficial
WhatsApp number blocked — the day's total was never the problem.

Two ceilings apply, mirroring the daily ones:

  per session   how fast one number may send, which is what WhatsApp watches
  gateway-wide  how fast the whole install may send, however many numbers

A session's own limit overrides the per-session default; zero means inherit.
Zero on the gateway-wide limit means no overall ceiling.

Redis rather than the database, deliberately: unlike the daily counters, a rate
window is seconds long, so losing it to a cache flush costs at most one window
of throttling rather than a day's accounting.
"""

import frappe

from frappe_whatsapp_openwa.utils.settings import limit, session_limit


def _window_seconds(session_name: str = "") -> int:
	return session_limit(session_name, "send_rate_window_seconds", 60)


def _session_allowance(session_name: str) -> int:
	"""Sends per window for this session — its own setting, else the default."""
	return session_limit(session_name, "send_rate_max", 20)


def _consume(key: str, allowance: int, window: int) -> bool:
	"""Take one slot from a sliding window. False when the window is full.

	The window is read before anything is added, so a refused send does not
	consume a slot it was never granted — otherwise a caller that keeps trying
	holds its own limit down and never recovers.
	"""
	if allowance <= 0:
		return True

	now = frappe.utils.now_datetime().timestamp()
	full_key = frappe.cache.make_key(key)

	pipe = frappe.cache.pipeline()
	pipe.zremrangebyscore(full_key, "-inf", now - window)
	pipe.zcard(full_key)
	_, used = pipe.execute()

	if used >= allowance:
		return False

	pipe = frappe.cache.pipeline()
	pipe.zadd(full_key, {f"{now}": now})
	pipe.expire(full_key, window)
	pipe.execute()
	return True


def consume_send_slot(session_name: str) -> bool:
	"""Claim the right to send one message on this session right now.

	Both ceilings must allow it. The gateway-wide window is taken first so a
	single busy session cannot exhaust the install's budget before the others
	are considered.
	"""
	if not session_name:
		return True

	window = _window_seconds(session_name)

	gateway_allowance = limit("gateway_send_rate_max", 0, zero_means_unlimited=True)
	if gateway_allowance and not _consume("openwa:sendrate:gateway", gateway_allowance, window):
		return False

	return _consume(f"openwa:sendrate:session:{session_name}", _session_allowance(session_name), window)


def has_rate_headroom(session_name: str) -> bool:
	"""True if this session could send right now, without taking a slot.

	Selection asks this of several sessions before choosing one; only the chosen
	session then consumes. Checking with consume_send_slot instead would spend a
	slot on every session it merely considered.
	"""
	if not session_name:
		return True

	allowance = _session_allowance(session_name)
	if allowance <= 0:
		return True

	try:
		now = frappe.utils.now_datetime().timestamp()
		key = frappe.cache.make_key(f"openwa:sendrate:session:{session_name}")
		pipe = frappe.cache.pipeline()
		pipe.zremrangebyscore(key, "-inf", now - _window_seconds(session_name))
		pipe.zcard(key)
		_, used = pipe.execute()
		return used < allowance
	except Exception:
		# Redis trouble must not make every session look unusable.
		return True
