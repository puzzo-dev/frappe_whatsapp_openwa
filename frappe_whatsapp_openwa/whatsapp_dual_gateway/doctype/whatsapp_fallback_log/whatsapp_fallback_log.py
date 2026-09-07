import frappe
from frappe.model.document import Document


class WhatsAppFallbackLog(Document):
	pass


def on_doctype_update():
	"""The weekly purge filters on triggered_at alone."""
	frappe.db.add_index("WhatsApp Fallback Log", ["triggered_at"])
