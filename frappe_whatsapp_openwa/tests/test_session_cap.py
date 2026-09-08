"""Daily cap accounting (prompt1 Findings 6 and 9)."""

import unittest
from unittest.mock import MagicMock, patch

_CAP = "frappe_whatsapp_openwa.utils.session_cap"
_WEBHOOK = "frappe_whatsapp_openwa.api.webhook"


class TestReserveSlot(unittest.TestCase):
	def _reserve(self, row_count, limit=0, rate_ok=True):
		frappe_mock = MagicMock()
		frappe_mock.db.get_single_value.return_value = limit
		frappe_mock.db.sql.side_effect = [None, [[row_count]]]
		# Pace is checked before volume; these cases are about the daily caps.
		with patch(f"{_CAP}.frappe", frappe_mock), \
			patch("frappe_whatsapp_openwa.utils.send_rate.consume_send_slot", return_value=rate_ok):
			from frappe_whatsapp_openwa.utils.session_cap import reserve_slot
			return reserve_slot("sess-001"), frappe_mock

	def test_claim_succeeds_when_under_cap(self):
		ok, _ = self._reserve(1)
		self.assertTrue(ok)

	def test_claim_fails_when_at_cap(self):
		"""The UPDATE matches nothing once the counter has reached the cap."""
		ok, _ = self._reserve(0)
		self.assertFalse(ok)

	def test_claim_is_a_single_conditional_update(self):
		"""Read-then-write is exactly the race being fixed — the cap test must be
		inside the UPDATE, not a separate SELECT."""
		_, m = self._reserve(1)
		sql = m.db.sql.call_args_list[0][0][0]
		self.assertIn("UPDATE", sql)
		self.assertIn("daily_soft_cap", sql)
		self.assertIn("messages_sent_today", sql)

	def test_rate_limit_refuses_before_the_daily_claim(self):
		"""A send refused for pace must not spend a slot from the day's total."""
		frappe_mock = MagicMock()
		with patch(f"{_CAP}.frappe", frappe_mock), \
			patch("frappe_whatsapp_openwa.utils.send_rate.consume_send_slot", return_value=False):
			from frappe_whatsapp_openwa.utils.session_cap import reserve_slot
			self.assertFalse(reserve_slot("sess-001"))
		frappe_mock.db.sql.assert_not_called()

	def test_blank_session_is_a_no_op(self):
		frappe_mock = MagicMock()
		with patch(f"{_CAP}.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.session_cap import reserve_slot
			self.assertTrue(reserve_slot(""))
		frappe_mock.db.sql.assert_not_called()

	def test_gateway_ceiling_is_tested_in_the_same_statement(self):
		"""Reading the total and then claiming would be the check-then-act race
		this function exists to avoid, so the sum is a derived table inside the
		UPDATE rather than a separate SELECT."""
		_, m = self._reserve(1, limit=500)
		sql = m.db.sql.call_args_list[0][0][0]
		self.assertIn("SUM(messages_sent_today)", sql)
		self.assertIn("daily_soft_cap", sql)
		self.assertEqual(m.db.sql.call_args_list[0][0][1]["limit"], 500)
		self.assertEqual(m.db.sql.call_count, 2, "one UPDATE plus ROW_COUNT, nothing else")

	def test_zero_ceiling_means_no_ceiling(self):
		_, m = self._reserve(1, limit=0)
		self.assertEqual(m.db.sql.call_args_list[0][0][1]["limit"], 0)

	def test_unreadable_setting_does_not_block_sending(self):
		"""A missing or misconfigured ceiling must never stop messages going out."""
		frappe_mock = MagicMock()
		frappe_mock.db.get_single_value.side_effect = Exception("no such field")
		frappe_mock.db.sql.side_effect = [None, [[1]]]
		with patch(f"{_CAP}.frappe", frappe_mock), \
			patch("frappe_whatsapp_openwa.utils.send_rate.consume_send_slot", return_value=True):
			from frappe_whatsapp_openwa.utils.session_cap import reserve_slot
			self.assertTrue(reserve_slot("sess-001"))


class TestReleaseSlot(unittest.TestCase):
	def test_release_floors_at_zero(self):
		frappe_mock = MagicMock()
		with patch(f"{_CAP}.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.session_cap import release_slot
			release_slot("sess-001")
		self.assertIn("GREATEST", frappe_mock.db.sql.call_args[0][0])

	def test_rate_limit_refuses_before_the_daily_claim(self):
		"""A send refused for pace must not spend a slot from the day's total."""
		frappe_mock = MagicMock()
		with patch(f"{_CAP}.frappe", frappe_mock), \
			patch("frappe_whatsapp_openwa.utils.send_rate.consume_send_slot", return_value=False):
			from frappe_whatsapp_openwa.utils.session_cap import reserve_slot
			self.assertFalse(reserve_slot("sess-001"))
		frappe_mock.db.sql.assert_not_called()

	def test_blank_session_is_a_no_op(self):
		frappe_mock = MagicMock()
		with patch(f"{_CAP}.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.session_cap import release_slot
			release_slot("")
		frappe_mock.db.sql.assert_not_called()


class TestResolveAccountForSession(unittest.TestCase):
	"""Finding 6: multi-session accounts resolved to nothing."""

	def _resolve(self, session_row, legacy=None):
		frappe_mock = MagicMock()
		frappe_mock.db.get_value.side_effect = [session_row, legacy]
		with patch(f"{_WEBHOOK}.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _resolve_account_for_session
			return _resolve_account_for_session("gw-sess-1"), frappe_mock

	def test_resolves_via_linked_whatsapp_account(self):
		"""The current link — this returned None before."""
		account, m = self._resolve({"name": "S1", "linked_whatsapp_account": "WA-ACC-A"})
		self.assertEqual(account, "WA-ACC-A")

	def test_falls_back_to_legacy_extension_link(self):
		account, _ = self._resolve({"name": "S1", "linked_whatsapp_account": None}, legacy="WA-ACC-LEGACY")
		self.assertEqual(account, "WA-ACC-LEGACY")

	def test_unknown_session_returns_none(self):
		frappe_mock = MagicMock()
		frappe_mock.db.get_value.side_effect = [None, None]
		with patch(f"{_WEBHOOK}.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _resolve_account_for_session
			self.assertIsNone(_resolve_account_for_session("nope"))


if __name__ == "__main__":
	unittest.main()
