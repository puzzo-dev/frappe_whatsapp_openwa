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


def reserve_slot(session_name: str) -> bool:
	"""Claim one message against the session's daily cap.

	Returns True if the slot was claimed, False if the session is already at
	its cap. A cap of 0 (or unset) means unlimited — the counter still moves so
	reporting stays accurate, but it never blocks.
	"""
	if not session_name:
		return True

	frappe.db.sql(
		"""UPDATE `tabOpenWA Session`
		   SET messages_sent_today = COALESCE(messages_sent_today, 0) + 1
		   WHERE name = %s
		     AND (daily_soft_cap IS NULL
		          OR daily_soft_cap = 0
		          OR COALESCE(messages_sent_today, 0) < daily_soft_cap)""",
		session_name,
	)
	# ROW_COUNT() reflects the UPDATE immediately preceding it on this
	# connection, so the claim and the test for it cannot be interleaved.
	return frappe.db.sql("SELECT ROW_COUNT()", as_list=True)[0][0] > 0


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
