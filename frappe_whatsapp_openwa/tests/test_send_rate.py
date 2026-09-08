"""Send rate is a different control from a daily cap.

The daily cap bounds how much a number sends; the rate bounds how fast. A
campaign that respects 1000/day by firing all of them in two minutes is exactly
what gets an unofficial WhatsApp number blocked, and the daily total never
noticed.

These assert behaviour through a window double rather than which Redis commands
were issued — see tests/fake_window.
"""

import unittest
from unittest.mock import MagicMock, patch

from frappe_whatsapp_openwa.tests.fake_window import FakeWindow

_MODULE = "frappe_whatsapp_openwa.utils.send_rate"
_WINDOW = "frappe_whatsapp_openwa.utils.sliding_window"

_SESSION_KEY = "openwa:sendrate:session:sess-1"
_GATEWAY_KEY = "openwa:sendrate:gateway"


class _Harness:
	def __init__(self, used=0, session_allowance=0, default_allowance=20,
	             gateway_allowance=0, window=60, gateway_used=0):
		self.window = FakeWindow()
		self.window.preload(_SESSION_KEY, used)
		self.window.preload(_GATEWAY_KEY, gateway_used)
		self.per_session = {
			"send_rate_window_seconds": window,
			"send_rate_max": session_allowance or default_allowance,
		}
		self.gateway_allowance = gateway_allowance

	def __enter__(self):
		self._patches = [
			patch(f"{_WINDOW}.take_slot", side_effect=self.window.take_slot),
			patch(f"{_WINDOW}.calls_in_window", side_effect=self.window.calls_in_window),
			patch(f"{_MODULE}.session_limit",
			      side_effect=lambda name, f, d, **k: self.per_session[f]),
			patch(f"{_MODULE}.limit", side_effect=lambda f, d, **k: self.gateway_allowance),
			patch(f"{_MODULE}.frappe", MagicMock()),
		]
		for p in self._patches:
			p.start()
		return self

	def __exit__(self, *exc):
		for p in self._patches:
			p.stop()

	def consume(self, session="sess-1"):
		from frappe_whatsapp_openwa.utils.send_rate import consume_send_slot

		return consume_send_slot(session)

	def used(self, key=_SESSION_KEY):
		return len(self.window.entries.get(key, []))


class TestConsumeSendSlot(unittest.TestCase):
	def test_under_the_rate_is_allowed(self):
		with _Harness(used=5, default_allowance=20) as h:
			self.assertTrue(h.consume())

	def test_at_the_rate_is_refused(self):
		with _Harness(used=20, default_allowance=20) as h:
			self.assertFalse(h.consume())

	def test_refused_send_does_not_consume_a_slot(self):
		"""Otherwise a caller that keeps retrying holds its own limit down and
		never recovers."""
		with _Harness(used=20, default_allowance=20) as h:
			h.consume()
			self.assertEqual(h.used(), 20, "a refused send must take nothing")

	def test_allowed_send_records_itself(self):
		with _Harness(used=1, default_allowance=20) as h:
			h.consume()
			self.assertEqual(h.used(), 2)

	def test_session_setting_overrides_the_default(self):
		with _Harness(used=6, session_allowance=5, default_allowance=100) as h:
			self.assertFalse(h.consume(), "the session's own tighter limit must win")

	def test_unset_session_value_falls_back_to_the_shipped_default(self):
		"""session_limit returns the fallback when the field is 0 or unset."""
		with _Harness(used=6, session_allowance=0, default_allowance=5) as h:
			self.assertFalse(h.consume())

	def test_zero_means_no_per_session_rate(self):
		with _Harness(used=9999, session_allowance=0, default_allowance=0) as h:
			self.assertTrue(h.consume())

	def test_blank_session_is_a_no_op(self):
		with _Harness() as h:
			self.assertTrue(h.consume(session=""))
			self.assertEqual(h.used(), 0)

	def test_old_entries_leave_the_window(self):
		"""A sliding window, not a counter that only resets on expiry."""
		with _Harness(used=20, default_allowance=20) as h:
			self.assertFalse(h.consume())
			h.window.now += 61  # the whole window has slid past
			self.assertTrue(h.consume())

	def test_gateway_ceiling_refuses_before_the_session_is_touched(self):
		"""A full gateway window must not spend one of the session's slots."""
		with _Harness(used=0, default_allowance=20,
		              gateway_allowance=5, gateway_used=5) as h:
			self.assertFalse(h.consume())
			self.assertEqual(h.used(_SESSION_KEY), 0)


class TestHeadroomTakesNothing(unittest.TestCase):
	def test_checking_headroom_does_not_consume(self):
		"""Selection asks several sessions; only the chosen one should pay."""
		from frappe_whatsapp_openwa.utils.send_rate import has_rate_headroom

		with _Harness(used=3, default_allowance=20) as h:
			self.assertTrue(has_rate_headroom("sess-1"))
			self.assertEqual(h.used(), 3)


if __name__ == "__main__":
	unittest.main()
