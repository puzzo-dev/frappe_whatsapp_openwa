"""WhatsApp Templates controller subclass.

Registered via hooks.py:
    override_doctype_class = {
        "WhatsApp Templates": "frappe_whatsapp_openwa.overrides.whatsapp_templates.WhatsAppTemplatesDualGateway"
    }

Problem: upstream validate()/after_insert() push every template to Meta's
Graph API (message_templates endpoint) using the linked account's token.
Accounts that exist purely to send via OpenWA have no valid Meta credentials,
so template creation fails and the template can never be used in a
WhatsApp Notification.

Fix: when the template's WhatsApp Account is OpenWA-routed (its Provider
Extension has default_provider == "OpenWA"), the template is saved locally
without any Meta round-trip. Sending never touches Meta either — the OpenWA
send path flattens the template to plain text client-side
(translators/template_flattener.py) and posts it to the OpenWA gateway.

For Meta-routed and Hybrid accounts the upstream behaviour is preserved
unchanged (Hybrid keeps the Meta push so the Meta fallback path has a real
approved template to reference).

The upstream frappe_whatsapp app itself is never modified — this is a
runtime class override, the same mechanism used for WhatsApp Message and
WhatsApp Notification.
"""

from __future__ import annotations

import frappe

try:
	from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_templates.whatsapp_templates import (
		WhatsAppTemplates as _UpstreamBase,
	)
except ImportError:
	from frappe.model.document import Document as _UpstreamBase  # type: ignore[assignment]


class WhatsAppTemplatesDualGateway(_UpstreamBase):
	def validate(self):
		self.set_whatsapp_account()
		if self._is_openwa_account():
			self._validate_local()
			return
		super().validate()

	def after_insert(self):
		if self._is_openwa_account():
			self._persist_local()
			return
		super().after_insert()

	def update_template(self):
		"""Upstream pushes edits to Meta on every save — skip for local templates."""
		if self._is_openwa_account():
			return
		super().update_template()

	def on_trash(self):
		"""Upstream deletes the template on Meta — skip for local templates."""
		if self._is_openwa_account():
			return
		super().on_trash()

	# ── Local (OpenWA) template handling ─────────────────────────────────

	def _validate_local(self) -> None:
		"""Local-only validate: language code + status, no Meta API calls.

		Media header uploads (get_session_id/get_media_id) are Meta-only and
		are skipped; the OpenWA flattener renders text headers only.
		"""
		if not self.language_code or self.has_value_changed("language"):
			lang_code = frappe.db.get_value("Language", self.language) or "en"
			self.language_code = lang_code.replace("-", "_")

		# No Meta approval flow for session-based sending — mark usable
		# immediately so list views and filters stay meaningful.
		if not self.status:
			self.status = "APPROVED"

	def _persist_local(self) -> None:
		if self.template_name:
			self.actual_name = self.template_name.lower().replace(" ", "_")
		if not self.id:
			self.id = f"local-{self.name}"
		if not self.status:
			self.status = "APPROVED"
		self.db_update()

	def _is_openwa_account(self) -> bool:
		"""True when the template's account defaults to the OpenWA provider."""
		account = self.whatsapp_account
		if not account:
			return False
		provider = frappe.db.get_value(
			"WhatsApp Account Provider Extension", account, "default_provider"
		)
		return provider == "OpenWA"
