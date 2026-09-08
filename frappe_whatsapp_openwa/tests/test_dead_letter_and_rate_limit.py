"""Unit tests for dead-letter handling and Meta rate limiting."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from frappe_whatsapp_openwa.queue.worker import _handle_dispatch_failure
from frappe_whatsapp_openwa.utils.rate_limiter import check_rate_limit


class TestDeadLetterHandling(unittest.TestCase):

	@patch("frappe_whatsapp_openwa.queue.worker.frappe")
	def test_dead_letter_created_after_max_retries(self, mock_frappe):
		mock_frappe.utils.now.return_value = "2026-08-01 10:00:00"
		# as_dict=True returns frappe._dict — attribute access, not dict keys.
		mock_frappe.db.get_value.return_value = SimpleNamespace(
			account="WA-001",
			recipient="1234567890",
			message_type="text",
			payload="{}",
			enqueued_at="2026-08-01 09:00:00",
		)

		# _BACKOFF_MINUTES has 6 entries, so attempts=6 means exhausted.
		_handle_dispatch_failure("WAOQ-2026-00001", "API timeout", attempts=6)

		mock_frappe.db.set_value.assert_called_once()
		updates = mock_frappe.db.set_value.call_args.args[2]
		self.assertEqual(updates["status"], "Failed")
		mock_frappe.get_doc.assert_called_once()
		inserted = mock_frappe.get_doc.return_value
		inserted.insert.assert_called_once_with(ignore_permissions=True)

	@patch("frappe_whatsapp_openwa.queue.worker.frappe")
	def test_retry_scheduled_before_max_retries(self, mock_frappe):
		mock_frappe.utils.add_to_date.return_value = "2026-08-01 10:05:00"
		mock_frappe.utils.now.return_value = "2026-08-01 10:00:00"

		_handle_dispatch_failure("WAOQ-2026-00001", "API timeout", attempts=1)

		mock_frappe.db.set_value.assert_called_once()
		updates = mock_frappe.db.set_value.call_args.args[2]
		self.assertEqual(updates["status"], "Queued")
		self.assertEqual(updates["next_attempt_at"], "2026-08-01 10:05:00")


class TestMetaRateLimiter(unittest.TestCase):

	def _limiter(self, mock_frappe, max_calls, window, already_used):
		"""Run check_rate_limit against a window double holding `already_used` calls."""
		from frappe_whatsapp_openwa.tests.fake_window import FakeWindow

		# settings.get(...) is used in the rate limiter — a plain dict matches that contract.
		mock_frappe.get_single.return_value = {
			"meta_rate_limit_window_seconds": window,
			"meta_rate_limit_max_calls": max_calls,
		}
		fake = FakeWindow()
		key = "openwa:ratelimit:meta:meta_send:WA-001"
		fake.preload(key, already_used)
		with patch("frappe_whatsapp_openwa.utils.sliding_window.take_slot",
		           side_effect=fake.take_slot):
			return check_rate_limit("WA-001"), fake, key

	@patch("frappe_whatsapp_openwa.utils.rate_limiter.frappe")
	def test_rate_limit_allows_within_window(self, mock_frappe):
		(allowed, context), _, _ = self._limiter(mock_frappe, 100, 60, already_used=0)

		self.assertTrue(allowed)
		self.assertEqual(context["max_calls"], 100)
		self.assertEqual(context["current_calls"], 1, "the granted call counts itself")

	@patch("frappe_whatsapp_openwa.utils.rate_limiter.frappe")
	def test_rate_limit_blocks_when_window_full(self, mock_frappe):
		(allowed, context), _, _ = self._limiter(mock_frappe, 2, 60, already_used=2)

		self.assertFalse(allowed)
		self.assertEqual(context["current_calls"], 2)

	@patch("frappe_whatsapp_openwa.utils.rate_limiter.frappe")
	def test_denied_call_does_not_consume_a_slot(self, mock_frappe):
		"""The regression: the entry was added before the count was checked, so a
		refused caller kept its own window full and could never recover."""
		_, fake, key = self._limiter(mock_frappe, 2, 60, already_used=2)

		self.assertEqual(len(fake.entries[key]), 2)

	@patch("frappe_whatsapp_openwa.utils.rate_limiter.frappe")
	def test_granted_call_consumes_exactly_one(self, mock_frappe):
		_, fake, key = self._limiter(mock_frappe, 10, 60, already_used=4)

		self.assertEqual(len(fake.entries[key]), 5)
