"""Unit tests for dead-letter handling and Meta rate limiting."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from frappe_whatsapp_openwa.queue.worker import _handle_dispatch_failure
from frappe_whatsapp_openwa.utils.rate_limiter import check_rate_limit


class TestDeadLetterHandling(unittest.TestCase):

	@patch("frappe_whatsapp_openwa.queue.worker.frappe")
	def test_dead_letter_created_after_max_retries(self, mock_frappe):
		mock_frappe.utils.now.return_value = "2026-08-01 10:00:00"
		mock_frappe.db.get_value.return_value = {
			"account": "WA-001",
			"recipient": "1234567890",
			"message_type": "text",
			"payload": "{}",
			"enqueued_at": "2026-08-01 09:00:00",
		}

		# _BACKOFF_MINUTES has 6 entries, so attempts=6 means exhausted.
		_handle_dispatch_failure("WAOQ-2026-00001", "API timeout", attempts=6)

		mock_frappe.db.set_value.assert_called_once()
		call_kwargs = mock_frappe.db.set_value.call_args.kwargs
		self.assertEqual(call_kwargs["status"], "Failed")
		mock_frappe.get_doc.assert_called_once()
		inserted = mock_frappe.get_doc.return_value
		inserted.insert.assert_called_once_with(ignore_permissions=True)

	@patch("frappe_whatsapp_openwa.queue.worker.frappe")
	def test_retry_scheduled_before_max_retries(self, mock_frappe):
		mock_frappe.utils.add_to_date.return_value = "2026-08-01 10:05:00"
		mock_frappe.utils.now.return_value = "2026-08-01 10:00:00"

		_handle_dispatch_failure("WAOQ-2026-00001", "API timeout", attempts=1)

		mock_frappe.db.set_value.assert_called_once()
		call_kwargs = mock_frappe.db.set_value.call_args.kwargs
		self.assertEqual(call_kwargs["status"], "Queued")
		self.assertEqual(call_kwargs["next_attempt_at"], "2026-08-01 10:05:00")


class TestMetaRateLimiter(unittest.TestCase):

	@patch("frappe_whatsapp_openwa.utils.rate_limiter.frappe")
	def test_rate_limit_allows_within_window(self, mock_frappe):
		mock_frappe.get_single.return_value = MagicMock(
			meta_rate_limit_window_seconds=60,
			meta_rate_limit_max_calls=100,
		)
		mock_frappe.get_conf.return_value = {}
		mock_frappe.cache.return_value.pipeline.return_value.execute.return_value = [
			None, [], None, None
		]
		mock_frappe.utils.now_datetime.return_value.timestamp.return_value = 1000.0

		allowed, context = check_rate_limit("WA-001")

		self.assertTrue(allowed)
		self.assertEqual(context["max_calls"], 100)
		self.assertEqual(context["current_calls"], 0)

	@patch("frappe_whatsapp_openwa.utils.rate_limiter.frappe")
	def test_rate_limit_blocks_when_window_full(self, mock_frappe):
		mock_frappe.get_single.return_value = MagicMock(
			meta_rate_limit_window_seconds=60,
			meta_rate_limit_max_calls=2,
		)
		mock_frappe.get_conf.return_value = {}
		mock_frappe.cache.return_value.pipeline.return_value.execute.return_value = [
			None, [("a", 1), ("b", 2)], None, None
		]
		mock_frappe.utils.now_datetime.return_value.timestamp.return_value = 1000.0

		allowed, context = check_rate_limit("WA-001")

		self.assertFalse(allowed)
		self.assertEqual(context["current_calls"], 2)
