import frappe
from frappe.model.document import Document


class WhatsAppOutboundQueue(Document):
	pass


def on_doctype_update():
	"""Indexes for the three queries the worker and purge run constantly.

	The batch SELECT filters on status and next_attempt_at then orders by
	enqueued_at; the stale reaper filters on status and last_attempt_at; the
	weekly purge filters on status and modified. Only `status` was indexed, and
	on its own it is close to useless here — a queue is overwhelmingly rows of
	one or two statuses, so the index selects most of the table and the server
	still scans it to apply the date test.
	"""
	frappe.db.add_index("WhatsApp Outbound Queue", ["status", "next_attempt_at", "enqueued_at"])
	frappe.db.add_index("WhatsApp Outbound Queue", ["status", "last_attempt_at"])
	frappe.db.add_index("WhatsApp Outbound Queue", ["status", "modified"])
