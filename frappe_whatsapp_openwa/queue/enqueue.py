"""Enqueue an outbound message for later delivery when the session is unhealthy."""

from __future__ import annotations

import frappe


def enqueue_message(
	account: str,
	recipient: str,
	message_type: str,
	payload: dict,
	requested_provider: str = "openwa",
	max_age_minutes: int = 15,
) -> str:
	"""Create a WhatsApp Outbound Queue record. Returns the doc name."""
	doc = frappe.new_doc("WhatsApp Outbound Queue")
	doc.account = account
	doc.recipient = recipient
	doc.message_type = message_type
	doc.requested_provider = requested_provider
	doc.status = "Queued"
	doc.enqueued_at = frappe.utils.now()
	doc.max_age_minutes = max_age_minutes
	doc.payload = frappe.as_json(payload)
	doc.attempts = 0
	# db_insert, not insert: a queue row is transient bookkeeping with no child
	# tables and no validation of its own, but insert() runs the full document
	# lifecycle — including the wildcard "*" doc_events that frappe, erpnext and
	# frappe_whatsapp all register, none of which have anything to say about it.
	# db_insert still assigns the name from the naming series and fills
	# creation/modified/owner, so the row is identical to the one insert() wrote.
	doc.db_insert()
	return doc.name
