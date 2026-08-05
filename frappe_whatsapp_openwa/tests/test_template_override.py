"""Unit tests for overrides/whatsapp_templates.py — Frappe is stubbed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestWhatsAppTemplatesDualGateway:
	def _make_doc(self, account="WA-OpenWA-1", is_openwa=True):
		"""Build a mock template doc with the dual-gateway mixin methods."""
		from frappe_whatsapp_openwa.overrides.whatsapp_templates import WhatsAppTemplatesDualGateway

		doc = MagicMock(spec=WhatsAppTemplatesDualGateway)
		doc.whatsapp_account = account
		doc.template_name = "Welcome Message"
		doc.language = "English"
		doc.language_code = None
		doc.status = None
		doc.name = "TPL-001"
		doc.id = None
		doc.actual_name = None
		# Wire up the real methods
		doc._is_openwa_account = WhatsAppTemplatesDualGateway._is_openwa_account.__get__(doc)
		doc._validate_local = WhatsAppTemplatesDualGateway._validate_local.__get__(doc)
		doc._persist_local = WhatsAppTemplatesDualGateway._persist_local.__get__(doc)
		doc.has_value_changed = MagicMock(return_value=False)
		doc.db_update = MagicMock()
		return doc, is_openwa

	def test_is_openwa_account_true(self):
		doc, _ = self._make_doc(account="WA-OpenWA-1", is_openwa=True)
		with patch("frappe_whatsapp_openwa.overrides.whatsapp_templates.frappe.db.get_value", return_value="OpenWA"):
			assert doc._is_openwa_account() is True

	def test_is_openwa_account_false(self):
		doc, _ = self._make_doc(account="WA-Meta-1", is_openwa=False)
		with patch("frappe_whatsapp_openwa.overrides.whatsapp_templates.frappe.db.get_value", return_value="Meta Cloud API"):
			assert doc._is_openwa_account() is False

	def test_is_openwa_account_no_account(self):
		doc, _ = self._make_doc(account=None)
		assert doc._is_openwa_account() is False

	def test_validate_local_sets_language_and_status(self):
		doc, _ = self._make_doc()
		with patch("frappe_whatsapp_openwa.overrides.whatsapp_templates.frappe.db.get_value", return_value="en"):
			doc._validate_local()
		assert doc.language_code == "en"
		assert doc.status == "APPROVED"

	def test_validate_local_preserves_existing_status(self):
		doc, _ = self._make_doc()
		doc.status = "DRAFT"
		with patch("frappe_whatsapp_openwa.overrides.whatsapp_templates.frappe.db.get_value", return_value="en"):
			doc._validate_local()
		assert doc.status == "DRAFT"

	def test_persist_local_sets_fields(self):
		doc, _ = self._make_doc()
		doc._persist_local()
		assert doc.actual_name == "welcome_message"
		assert doc.id == "local-TPL-001"
		assert doc.status == "APPROVED"
		doc.db_update.assert_called_once()

	def test_persist_local_preserves_existing_id(self):
		doc, _ = self._make_doc()
		doc.id = "existing-123"
		doc._persist_local()
		assert doc.id == "existing-123"

	def test_validate_skips_meta_for_openwa(self):
		"""validate() should call _validate_local and return early for OpenWA accounts."""
		from frappe_whatsapp_openwa.overrides.whatsapp_templates import WhatsAppTemplatesDualGateway
		doc, _ = self._make_doc()
		doc.set_whatsapp_account = MagicMock()
		doc.validate = WhatsAppTemplatesDualGateway.validate.__get__(doc)
		with (
			patch("frappe_whatsapp_openwa.overrides.whatsapp_templates.frappe.db.get_value", return_value="OpenWA"),
			patch.object(doc, "_validate_local") as mock_local,
		):
			doc.validate()
		mock_local.assert_called_once()

	def test_validate_calls_super_for_meta(self):
		"""validate() should not call _validate_local for Meta accounts."""
		from frappe_whatsapp_openwa.overrides.whatsapp_templates import WhatsAppTemplatesDualGateway
		doc, _ = self._make_doc()
		doc.set_whatsapp_account = MagicMock()
		doc.validate = WhatsAppTemplatesDualGateway.validate.__get__(doc)
		with (
			patch("frappe_whatsapp_openwa.overrides.whatsapp_templates.frappe.db.get_value", return_value="Meta Cloud API"),
			patch.object(doc, "_validate_local") as mock_local,
		):
			try:
				doc.validate()
			except Exception:
				pass  # super().validate() will fail since base is a stub
		mock_local.assert_not_called()
