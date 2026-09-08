"""Pairing-code request budget (OWA-08).

WhatsApp throttles device-linking attempts and can block a number that requests
too many, so an unbounded loop here can make the number unlinkable.
"""

import unittest
from unittest.mock import MagicMock, patch

_MODULE = "frappe_whatsapp_openwa.api.session"


class TestPairingCodeRateLimit(unittest.TestCase):
	def _run(self, count, ttl=60):
		frappe_mock = MagicMock()
		frappe_mock._ = lambda s: s
		frappe_mock.TooManyRequestsError = type("TooManyRequestsError", (Exception,), {})
		frappe_mock.throw = MagicMock(side_effect=frappe_mock.TooManyRequestsError)
		frappe_mock.cache.make_key = lambda k, **kw: k
		pipe = frappe_mock.cache.pipeline.return_value
		pipe.execute.return_value = (count, ttl)

		# The budget lives in OpenWA Gateway Settings now, so the test states it
		# rather than inheriting whatever a mocked settings read happens to return.
		with patch(f"{_MODULE}.frappe", frappe_mock), \
			patch(f"{_MODULE}.session_limit", side_effect=lambda name, f, d, **k: {"pairing_code_max_requests": 5, "pairing_code_window_seconds": 600}[f]):
			from frappe_whatsapp_openwa.api.session import _check_pairing_code_rate_limit
			_check_pairing_code_rate_limit("OWA-SESS-001")
		return frappe_mock

	def test_first_request_allowed(self):
		self._run(count=1)  # must not throw

	def test_fifth_request_allowed(self):
		self._run(count=5)  # budget is inclusive

	def test_sixth_request_rejected(self):
		frappe_mock = MagicMock()
		frappe_mock._ = lambda s: s
		exc = type("TooManyRequestsError", (Exception,), {})
		frappe_mock.TooManyRequestsError = exc
		frappe_mock.throw = MagicMock(side_effect=exc)
		frappe_mock.cache.make_key = lambda k, **kw: k
		frappe_mock.cache.pipeline.return_value.execute.return_value = (6, 60)
		with patch(f"{_MODULE}.frappe", frappe_mock), \
			patch(f"{_MODULE}.session_limit", side_effect=lambda name, f, d, **k: {"pairing_code_max_requests": 5, "pairing_code_window_seconds": 600}[f]):
			from frappe_whatsapp_openwa.api.session import _check_pairing_code_rate_limit
			with self.assertRaises(exc):
				_check_pairing_code_rate_limit("OWA-SESS-001")

	def test_missing_ttl_sets_expiry(self):
		"""Without this the counter would never reset and would lock the session out."""
		m = self._run(count=1, ttl=-1)
		m.cache.expire.assert_called_once()

	def test_live_window_not_extended(self):
		m = self._run(count=2, ttl=300)
		m.cache.expire.assert_not_called()

	def test_counter_is_per_session(self):
		m = self._run(count=1)
		self.assertIn("OWA-SESS-001", m.cache.pipeline.return_value.incr.call_args[0][0])


if __name__ == "__main__":
	unittest.main()
