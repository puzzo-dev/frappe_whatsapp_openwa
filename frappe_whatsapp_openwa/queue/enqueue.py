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
	doc.insert(ignore_permissions=True)
	return doc.name
