import frappe
from frappe.model.document import Document


class WhatsAppAccountProviderExtension(Document):
	def validate(self):
		if self.default_provider == "OpenWA":
			has_session = bool(self.openwa_session) or frappe.db.exists(
				"OpenWA Session", {"linked_whatsapp_account": self.linked_whatsapp_account}
			)
			if not has_session:
				frappe.throw(
					frappe._("An OpenWA Session must be linked when Default Provider is OpenWA.")
				)
