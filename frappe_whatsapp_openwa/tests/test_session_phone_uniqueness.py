"""One phone number drives one session.

The constraint has to be on a canonical form. A raw unique index would let
+2348012345678, 08012345678 and 2348012345678 through as three sessions all
pointed at the same WhatsApp account, which is the situation it exists to stop.
"""

import unittest
from unittest.mock import MagicMock, patch

_DOC = "frappe_whatsapp_openwa.whatsapp_dual_gateway.doctype.openwa_session.openwa_session"


class _Dict(dict):
    """frappe.db.get_value(..., as_dict=True) returns attribute-accessible rows."""

    __getattr__ = dict.get


def _session(phone, name=None):
    from frappe_whatsapp_openwa.whatsapp_dual_gateway.doctype.openwa_session.openwa_session import (
        OpenWASession,
    )

    s = object.__new__(OpenWASession)
    s.phone_number = phone
    s.name = name
    return s


class TestPhoneNormalisation(unittest.TestCase):
    def _normalise(self, phone, existing=None):
        frappe_mock = MagicMock()
        frappe_mock._ = lambda x: x
        frappe_mock.db.get_value.return_value = existing
        frappe_mock.throw = MagicMock(side_effect=ValueError)
        doc = _session(phone)
        with patch(f"{_DOC}.frappe", frappe_mock):
            doc._normalise_phone_number()
        return doc.phone_number

    def test_local_form_becomes_e164(self):
        self.assertEqual(self._normalise("08012345678"), "+2348012345678")

    def test_country_code_without_plus_becomes_e164(self):
        self.assertEqual(self._normalise("2348012345678"), "+2348012345678")

    def test_already_canonical_is_untouched(self):
        self.assertEqual(self._normalise("+2348012345678"), "+2348012345678")

    def test_all_three_forms_collapse_to_one_value(self):
        """This is what makes the unique index mean anything."""
        forms = ["08012345678", "2348012345678", "+2348012345678"]
        self.assertEqual(len({self._normalise(f) for f in forms}), 1)

    def test_blank_is_left_alone(self):
        self.assertEqual(self._normalise(""), "")

    def test_number_already_in_use_is_refused(self):
        existing = _Dict(name="OWA-1", session_name="Sales line")
        with self.assertRaises(ValueError):
            self._normalise("08012345678", existing=existing)

    def test_refusal_names_the_session_holding_it(self):
        """The unique index reports a collision; this says where to go."""
        frappe_mock = MagicMock()
        frappe_mock._ = lambda x: x
        frappe_mock.db.get_value.return_value = _Dict(name="OWA-1", session_name="Sales line")
        captured = {}

        def _throw(msg, **kw):
            captured["msg"] = msg
            raise ValueError(msg)

        frappe_mock.throw = _throw
        with patch(f"{_DOC}.frappe", frappe_mock):
            with self.assertRaises(ValueError):
                _session("08012345678")._normalise_phone_number()
        self.assertIn("Sales line", captured["msg"])
        self.assertIn("+2348012345678", captured["msg"])

    def test_lookup_excludes_the_document_itself(self):
        """Re-saving a session must not collide with its own row."""
        frappe_mock = MagicMock()
        frappe_mock._ = lambda x: x
        frappe_mock.db.get_value.return_value = None
        with patch(f"{_DOC}.frappe", frappe_mock):
            _session("+2348012345678", name="OWA-9")._normalise_phone_number()
        filters = frappe_mock.db.get_value.call_args[0][1]
        self.assertEqual(filters["name"], ("!=", "OWA-9"))


if __name__ == "__main__":
    unittest.main()
