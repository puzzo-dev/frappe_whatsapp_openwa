"""Guards around set_property_after_alert (OWA-11).

The write itself stays privileged — matching Frappe core, since the field and
value come from admin-only alert config. What must not be skipped are core's
guards, which the previous raw frappe.db.set_value implementation had none of.
"""

import unittest
from unittest.mock import MagicMock, patch

_MODULE = "frappe_whatsapp_openwa.overrides.notification"


def _alert(field="status", value="Notified"):
	from frappe_whatsapp_openwa.overrides.notification import WhatsAppNotificationDualGateway

	n = object.__new__(WhatsAppNotificationDualGateway)
	n.set_property_after_alert = field
	n.property_value = value
	n.doctype = "WhatsApp Notification"
	n.name = "ALERT-001"
	return n


def _target(submitted=False, allow_on_submit=0, in_update=False, fieldtype="Data"):
	doc = MagicMock()
	doc.docstatus.is_submitted.return_value = submitted
	df = MagicMock()
	df.allow_on_submit = allow_on_submit
	df.fieldtype = fieldtype
	doc.meta.get_field.return_value = df
	doc.flags.in_notification_update = in_update
	return doc


class TestApplySetProperty(unittest.TestCase):
	def _run(self, doc):
		frappe_mock = MagicMock()
		frappe_mock._ = lambda s: s
		frappe_mock.get_doc.return_value = doc
		frappe_mock.model.numeric_fieldtypes = ("Int", "Float", "Currency")
		frappe_mock.utils.cint = int
		with patch(f"{_MODULE}.frappe", frappe_mock):
			_alert()._apply_set_property({"doctype": "Sales Invoice", "name": "SI-001"})
		return doc

	def test_draft_document_is_updated(self):
		doc = self._run(_target(submitted=False))
		doc.save.assert_called_once()
		doc.set.assert_called_once_with("status", "Notified")

	def test_submitted_document_not_updated_without_allow_on_submit(self):
		"""Submitted records are immutable except on allow_on_submit fields."""
		doc = self._run(_target(submitted=True, allow_on_submit=0))
		doc.save.assert_not_called()

	def test_submitted_document_updated_when_allow_on_submit(self):
		doc = self._run(_target(submitted=True, allow_on_submit=1))
		doc.save.assert_called_once()

	def test_recursive_update_is_skipped(self):
		"""The save re-fires document events, which can re-enter this alert."""
		doc = self._run(_target(in_update=True))
		doc.save.assert_not_called()

	def test_recursion_flag_cleared_after_save(self):
		doc = self._run(_target())
		self.assertFalse(doc.flags.in_notification_update)

	def test_update_goes_through_save_not_raw_set_value(self):
		"""doc.save keeps validation, version history and the updater reference."""
		doc = self._run(_target())
		doc.reload.assert_called_once()
		self.assertEqual(doc.save.call_args.kwargs, {"ignore_permissions": True})


if __name__ == "__main__":
	unittest.main()
