"""Daily send-cap accounting for an OpenWA session.

The cap used to be enforced by reading `messages_sent_today`, sending, and then
incrementing it in a separate statement. Two concurrent senders could both read
a below-cap value and both send, so the cap could be exceeded by as many
messages as there were workers in flight.

Reading and claiming are one statement here instead: the UPDATE only matches
while the session is under its cap, so exactly one caller can take the last
slot. The claim is taken *before* the send, because a claim taken afterwards
cannot prevent anything — the message has already gone.

A claim that does not result in an OpenWA send is released again. If a process
dies between claiming and releasing, the session simply has one slot fewer for
the rest of the day; `reset_daily_message_counts` clears the counter at
midnight, so the leak cannot accumulate.
"""

import frappe


def _daily_send_limit() -> int:
	"""The gateway-wide ceiling for the day, or 0 for no ceiling."""
	# Coerced here rather than with frappe.utils.cint: this module is imported by
	# the queue worker, and pulling in a frappe submodule at import time breaks
	# anything that loads it without a full Frappe (the unit tests, for one).
	try:
		return int(frappe.db.get_single_value("OpenWA Gateway Settings", "daily_send_limit") or 0)
	except Exception:
		# A missing or misconfigured ceiling must never block sending.
		return 0


def reserve_slot(session_name: str) -> bool:
	"""Claim the right to send one message on this session.

	Four limits can stop it and they answer two different questions. How fast:
	the session's send rate and the gateway-wide send rate. How much: the
	session's Daily Soft Cap and the gateway-wide Daily Send Limit. A daily cap
	does not pace a campaign and a rate limit does not bound its total, so both
	kinds exist.

	Two limits apply and either can stop a send: the session's own Daily Soft
	Cap, which guards a single WhatsApp number, and the gateway-wide Daily Send
	Limit, which caps total OpenWA volume however many numbers are connected.
	Zero means no limit on either.

	Both are tested inside the one UPDATE. The gateway total is the sum of the
	same `messages_sent_today` counters the per-session cap uses, so there is no
	second counter to drift out of step and nothing to rebuild after a cache
	flush. Reading the sum first and then claiming would be the check-then-act
	race this function exists to avoid, so the sum is a derived table inside the
	statement instead — MariaDB will not let a subquery read the table being
	updated directly, but it will through one of these.

	Returns True if the slot was claimed, False if either ceiling is reached.
	"""
	if not session_name:
		return True

	# Pace before volume. The daily claim is the durable one, so the rate check
	# goes first: a refused send then leaves the day's counter untouched. The
	# reverse order would burn a day slot on a message the rate limiter was
	# about to refuse anyway.
	from frappe_whatsapp_openwa.utils.send_rate import consume_send_slot

	if not consume_send_slot(session_name):
		return False

	limit = _daily_send_limit()

	frappe.db.sql(
		"""UPDATE `tabOpenWA Session`
		   SET messages_sent_today = COALESCE(messages_sent_today, 0) + 1
		   WHERE name = %(session)s
		     AND (daily_soft_cap IS NULL
		          OR daily_soft_cap = 0
		          OR COALESCE(messages_sent_today, 0) < daily_soft_cap)
		     AND (%(limit)s = 0
		          OR (SELECT total FROM (
		                  SELECT COALESCE(SUM(messages_sent_today), 0) AS total
		                  FROM `tabOpenWA Session`
		              ) AS sent_today) < %(limit)s)""",
		{"session": session_name, "limit": limit},
	)
	# ROW_COUNT() reflects the UPDATE immediately preceding it on this
	# connection, so the claim and the test for it cannot be interleaved.
	claimed = frappe.db.sql("SELECT ROW_COUNT()", as_list=True)[0][0] > 0

	if claimed:
		# Last Message Sent existed as a read-only field that nothing ever wrote,
		# so it showed blank forever. This is the moment a send is granted, which
		# is what the field was asking about — and when warming a number up, "when
		# did this one last send" is exactly what an operator needs to see.
		frappe.db.set_value(
			"OpenWA Session", session_name, "last_message_sent",
			frappe.utils.now(), update_modified=False,
		)

	return claimed


def release_slot(session_name: str) -> None:
	"""Give back a slot claimed by reserve_slot when no OpenWA send happened."""
	if not session_name:
		return
	frappe.db.sql(
		"""UPDATE `tabOpenWA Session`
		   SET messages_sent_today = GREATEST(COALESCE(messages_sent_today, 0) - 1, 0)
		   WHERE name = %s""",
		session_name,
	)


def cap_reached(session_name: str) -> bool:
	"""Read-only check, for skipping work early.

	This is a hint, not the enforcement point — by the time a caller acts on it
	another sender may have taken the last slot. Enforcement is reserve_slot.
	"""
	if not session_name:
		return False
	row = frappe.db.get_value(
		"OpenWA Session",
		session_name,
		["messages_sent_today", "daily_soft_cap"],
		as_dict=True,
	)
	if not row:
		return False
	cap = row.daily_soft_cap or 0
	if cap == 0:
		return False
	return (row.messages_sent_today or 0) >= cap


def count_slot(session_name: str) -> None:
	"""Count a send that is never blocked by the cap.

	The notification path has always counted its sends without checking the cap
	first, so notifications register against the day's total but are never
	withheld because of it. That is left as-is deliberately — making the cap
	block business alerts is a behaviour change, not a race fix — but the
	counting itself lives here so there is one implementation of it.
	"""
	if not session_name:
		return
	frappe.db.sql(
		"UPDATE `tabOpenWA Session` SET messages_sent_today = COALESCE(messages_sent_today, 0) + 1 WHERE name = %s",
		session_name,
	)


def has_daily_headroom(session_name: str) -> bool:
	"""True if this session is still under both daily ceilings.

	Read-only, for choosing between sessions. reserve_slot remains the only
	thing that claims, so selection cannot consume anyone's budget.
	"""
	if not session_name:
		return True

	if cap_reached(session_name):
		return False

	ceiling = _daily_send_limit()
	if not ceiling:
		return True
	try:
		total = frappe.db.sql(
			"SELECT COALESCE(SUM(messages_sent_today), 0) FROM `tabOpenWA Session`"
		)[0][0]
		return int(total or 0) < ceiling
	except Exception:
		return True


def can_send(session_name: str) -> bool:
	"""True if this session has room on pace and on volume."""
	from frappe_whatsapp_openwa.utils.send_rate import has_rate_headroom

	return has_rate_headroom(session_name) and has_daily_headroom(session_name)
