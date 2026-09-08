"""Send rate is a different control from a daily cap.

The daily cap bounds how much a number sends; the rate bounds how fast. A
campaign that respects 1000/day by firing all of them in two minutes is exactly
what gets an unofficial WhatsApp number blocked, and the daily total never
noticed.
"""

import unittest
from unittest.mock import MagicMock, patch

_MODULE = "frappe_whatsapp_openwa.utils.send_rate"


class TestConsumeSendSlot(unittest.TestCase):
    def _consume(self, used=0, session_allowance=0, default_allowance=20,
                 gateway_allowance=0, window=60):
        frappe_mock = MagicMock()
        frappe_mock.cache.make_key = lambda k, **kw: k
        frappe_mock.db.get_value.return_value = session_allowance
        frappe_mock.utils.now_datetime.return_value.timestamp.return_value = 1000.0

        pipe = frappe_mock.cache.pipeline.return_value
        pipe.execute.side_effect = [(None, used), (None, None)]

        # Per-number limits come from the session; only the all-sessions
        # ceiling is still gateway-wide.
        per_session = {
            "send_rate_window_seconds": window,
            "send_rate_max": session_allowance or default_allowance,
        }
        with patch(f"{_MODULE}.frappe", frappe_mock), \
            patch(f"{_MODULE}.session_limit",
                  side_effect=lambda name, f, d, **k: per_session[f]), \
            patch(f"{_MODULE}.limit",
                  side_effect=lambda f, d, **k: gateway_allowance):
            from frappe_whatsapp_openwa.utils.send_rate import consume_send_slot
            return consume_send_slot("sess-1"), frappe_mock

    def test_under_the_rate_is_allowed(self):
        ok, _ = self._consume(used=5, default_allowance=20)
        self.assertTrue(ok)

    def test_at_the_rate_is_refused(self):
        ok, _ = self._consume(used=20, default_allowance=20)
        self.assertFalse(ok)

    def test_refused_send_does_not_consume_a_slot(self):
        """Otherwise a caller that keeps retrying holds its own limit down and
        never recovers."""
        _, m = self._consume(used=20, default_allowance=20)
        pipe = m.cache.pipeline.return_value
        pipe.zadd.assert_not_called()

    def test_allowed_send_records_itself(self):
        _, m = self._consume(used=1, default_allowance=20)
        m.cache.pipeline.return_value.zadd.assert_called_once()

    def test_session_setting_overrides_the_default(self):
        ok, _ = self._consume(used=6, session_allowance=5, default_allowance=100)
        self.assertFalse(ok, "the session's own tighter limit must win")

    def test_unset_session_value_falls_back_to_the_shipped_default(self):
        """session_limit returns the fallback when the field is 0 or unset."""
        ok, _ = self._consume(used=6, session_allowance=0, default_allowance=5)
        self.assertFalse(ok)

    def test_zero_means_no_per_session_rate(self):
        ok, _ = self._consume(used=9999, session_allowance=0, default_allowance=0)
        self.assertTrue(ok)

    def test_blank_session_is_a_no_op(self):
        frappe_mock = MagicMock()
        with patch(f"{_MODULE}.frappe", frappe_mock):
            from frappe_whatsapp_openwa.utils.send_rate import consume_send_slot
            self.assertTrue(consume_send_slot(""))
        frappe_mock.cache.pipeline.assert_not_called()

    def test_old_entries_leave_the_window(self):
        """A sliding window, not a counter that only resets on expiry."""
        _, m = self._consume(used=1)
        m.cache.pipeline.return_value.zremrangebyscore.assert_called_once()


if __name__ == "__main__":
    unittest.main()
