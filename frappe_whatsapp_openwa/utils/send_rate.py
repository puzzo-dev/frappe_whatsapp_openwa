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

	A refused send takes nothing, so a caller that keeps trying does not hold
	its own limit down and never recover. Reading and adding used to be two
	round trips, which left room for two workers to both see the last slot free
	and both take it — precisely under the load the limit exists for. It is one
	atomic operation now; see utils/sliding_window.
	"""
	from frappe_whatsapp_openwa.utils.sliding_window import take_slot

	granted, _ = take_slot(key, allowance, window)
	return granted


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


def release_send_slot(session_name: str) -> None:
	"""Undo a consume_send_slot when the message did not go out over OpenWA.

	A slot was taken from the session's window — and from the gateway-wide one
	— before the send was attempted. When the send then falls back to Meta or
	fails outright, nothing went out on this number, so holding those slots
	throttles a healthy session for messages it never sent. During a campaign
	with a Meta fallback that is most of them.

	Both windows are given back, in the reverse order they were taken.
	"""
	if not session_name:
		return

	from frappe_whatsapp_openwa.utils.sliding_window import give_back

	if _session_allowance(session_name) > 0:
		give_back(f"openwa:sendrate:session:{session_name}")
	if limit("gateway_send_rate_max", 0, zero_means_unlimited=True):
		give_back("openwa:sendrate:gateway")


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
		from frappe_whatsapp_openwa.utils.sliding_window import calls_in_window

		used = calls_in_window(
			f"openwa:sendrate:session:{session_name}", _window_seconds(session_name)
		)
		return used < allowance
	except Exception:
		# Redis trouble must not make every session look unusable.
		return True
