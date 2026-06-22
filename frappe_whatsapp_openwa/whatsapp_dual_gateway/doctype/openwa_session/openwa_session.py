import frappe
from frappe.model.document import Document


class OpenWASession(Document):
	def before_save(self):
		self.requires_human_attention = 1 if self.status in (
			"QR Required",
			"Restart Failed",
			"Banned",
		) else 0

	@frappe.whitelist()
	def restart_session(self):
		"""Trigger a manual restart of this session via the gateway."""
		from frappe_whatsapp_openwa.monitoring.self_healer import _issue_restart
		_issue_restart(self)
		self.restart_attempt_count = (self.restart_attempt_count or 0) + 1
		self.last_state_change = frappe.utils.now()
		self.save(ignore_permissions=True)
		return {"status": "restart_issued"}
