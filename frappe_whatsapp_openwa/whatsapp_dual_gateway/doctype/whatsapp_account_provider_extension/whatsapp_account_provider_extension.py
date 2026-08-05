import frappe
from frappe.model.document import Document


class WhatsAppAccountProviderExtension(Document):
	def validate(self):
		if self.default_provider != "OpenWA":
			return

		if self.openwa_session:
			return

		sessions = frappe.db.get_all(
			"OpenWA Session",
			filters={"linked_whatsapp_account": self.linked_whatsapp_account},
			fields=["name"],
			limit=1,
		)
		if not sessions:
			frappe.throw(
				frappe._(
					"At least one OpenWA Session must be linked to this account "
					"when Default Provider is OpenWA."
				)
			)
