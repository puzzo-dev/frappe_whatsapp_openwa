import frappe
from frappe.model.document import Document


class OpenWAWebhookLog(Document):
	pass


def on_doctype_update():
	"""Indexes for the purge and for the desk list's usual filters.

	The purge runs `WHERE processed = 1 AND received_at < %s`. Indexing
	`processed` alone would not help — it is a checkbox with two values — so the
	index leads with it only to reach the ordered `received_at` beside it.
	"""
	frappe.db.add_index("OpenWA Webhook Log", ["processed", "received_at"])
	frappe.db.add_index("OpenWA Webhook Log", ["session_id", "event_type"])
