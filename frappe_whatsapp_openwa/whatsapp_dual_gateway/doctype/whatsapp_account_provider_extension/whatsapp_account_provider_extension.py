import frappe
from frappe.model.document import Document


class WhatsAppAccountProviderExtension(Document):
	def validate(self):
		if self.default_provider == "OpenWA" and not self.openwa_session:
			frappe.throw(
				frappe._("An OpenWA Session must be linked when Default Provider is OpenWA.")
			)
