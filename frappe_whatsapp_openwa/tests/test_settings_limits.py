"""Limits come from the doctype, not from constants in the code.

Every limit the app enforces is a field on OpenWA Gateway Settings. The values
used to be constants next to the code that enforced them, so what an operator
could see was not what the code used — there was nothing to see at all.
"""

import unittest
from unittest.mock import MagicMock, patch

_MODULE = "frappe_whatsapp_openwa.utils.settings"


class TestLimit(unittest.TestCase):
    def _limit(self, stored, fallback=50, zero_unlimited=False):
        frappe_mock = MagicMock()
        frappe_mock.db.get_single_value.return_value = stored
        with patch(f"{_MODULE}.frappe", frappe_mock):
            from frappe_whatsapp_openwa.utils.settings import limit
            return limit("some_field", fallback, zero_means_unlimited=zero_unlimited)

    def test_stored_value_wins(self):
        self.assertEqual(self._limit(120), 120)

    def test_string_value_is_coerced(self):
        """Int fields can come back as strings from a Single."""
        self.assertEqual(self._limit("120"), 120)

    def test_unset_falls_back_to_shipped_default(self):
        self.assertEqual(self._limit(None), 50)

    def test_zero_is_unset_where_zero_would_stall_the_feature(self):
        """A batch size or retention window of zero disables the job rather
        than unbounding it, so zero means 'not configured'."""
        self.assertEqual(self._limit(0), 50)

    def test_zero_means_unlimited_where_that_is_meaningful(self):
        self.assertEqual(self._limit(0, zero_unlimited=True), 0)

    def test_negative_is_rejected(self):
        self.assertEqual(self._limit(-5), 50)

    def test_garbage_value_falls_back(self):
        self.assertEqual(self._limit("not a number"), 50)

    def test_unreadable_settings_never_break_the_caller(self):
        """Mid-migration or a broken Single must not stop the app working."""
        frappe_mock = MagicMock()
        frappe_mock.db.get_single_value.side_effect = Exception("no such doctype")
        with patch(f"{_MODULE}.frappe", frappe_mock):
            from frappe_whatsapp_openwa.utils.settings import limit
            self.assertEqual(limit("some_field", 7), 7)


if __name__ == "__main__":
    unittest.main()
