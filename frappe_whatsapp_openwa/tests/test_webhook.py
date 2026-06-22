"""Integration-style tests for api/webhook.py and utils/idempotency.py.

All Frappe globals are patched — no live site needed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch


# ─── Helpers ────────────────────────────────────────────────────────────────

def _sign(body: bytes, secret: str = "test-secret") -> str:
	return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload_bytes(event: str = "message", session_id: str = "sess-001", **extra) -> bytes:
	data = {"event": event, "sessionId": session_id, **extra}
	return json.dumps(data).encode()


def _make_frappe_mock(secret: str = "test-secret") -> MagicMock:
	"""Minimal frappe mock for webhook tests."""
	m = MagicMock()
	settings = MagicMock()
	settings.get_password.return_value = secret
	m.get_single.return_value = settings

	cache = MagicMock()
	pipe = MagicMock()
	pipe.execute.return_value = (1, 60)   # count=1, ttl=60
	cache.pipeline.return_value = pipe
	cache.get.return_value = None
	cache.exists.return_value = False
	m.cache.return_value = cache

	m.parse_json.side_effect = json.loads
	m.as_json.side_effect = json.dumps
	m.utils.now.return_value = "2026-06-22 00:00:00"
	m.get_doc.return_value = MagicMock()
	m.new_doc.return_value = MagicMock()
	m.db.get_value.return_value = None
	m.get_traceback.return_value = "traceback"
	m.log_error = MagicMock()

	# Exception classes — must be real types so frappe.throw can raise them.
	m.TooManyRequestsError = type("TooManyRequestsError", (Exception,), {})
	m.AuthenticationError = type("AuthenticationError", (Exception,), {})
	m.ValidationError = type("ValidationError", (Exception,), {})

	# Make frappe.throw actually raise so assertRaises works.
	def _throw(msg, exc_cls=None, **kw):
		raise (exc_cls or Exception)(msg)
	m.throw.side_effect = _throw
	return m


# ─── HMAC / _parse_and_verify ────────────────────────────────────────────────

class TestParseAndVerify(unittest.TestCase):
	def _call(self, body: bytes, signature: str, secret: str = "test-secret"):
		frappe_mock = _make_frappe_mock(secret)
		frappe_mock.request = MagicMock()
		frappe_mock.request.get_data.return_value = body
		frappe_mock.request.headers = {"X-OpenWA-Signature": signature}
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _parse_and_verify
			return _parse_and_verify(frappe_mock.get_single.return_value), frappe_mock

	def test_valid_signature_returns_parsed_payload(self):
		body = _payload_bytes("session.connected")
		sig = _sign(body)
		result, _ = self._call(body, sig)
		self.assertEqual(result["event"], "session.connected")

	def test_missing_secret_throws(self):
		body = _payload_bytes()
		frappe_mock = _make_frappe_mock()
		settings = MagicMock()
		settings.get_password.return_value = ""  # unconfigured
		frappe_mock.request = MagicMock()
		frappe_mock.request.get_data.return_value = body
		frappe_mock.request.headers = {}
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _parse_and_verify
			with self.assertRaises(Exception):
				_parse_and_verify(settings)

	def test_wrong_signature_throws(self):
		body = _payload_bytes()
		frappe_mock = _make_frappe_mock()
		frappe_mock.request = MagicMock()
		frappe_mock.request.get_data.return_value = body
		frappe_mock.request.headers = {"X-OpenWA-Signature": "badsig"}
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _parse_and_verify
			with self.assertRaises(Exception):
				_parse_and_verify(frappe_mock.get_single.return_value)


# ─── Rate limiter / _check_rate_limit ────────────────────────────────────────

class TestCheckRateLimit(unittest.TestCase):
	def _run(self, count: int, ttl: int, session_id: str = "sess-001"):
		frappe_mock = _make_frappe_mock()
		pipe = frappe_mock.cache.return_value.pipeline.return_value
		pipe.execute.return_value = (count, ttl)
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _check_rate_limit
			_check_rate_limit(session_id)
		return frappe_mock

	def test_count_1_does_not_raise(self):
		self._run(count=1, ttl=60)  # no assertion — must not throw

	def test_over_limit_raises(self):
		frappe_mock = _make_frappe_mock()
		pipe = frappe_mock.cache.return_value.pipeline.return_value
		pipe.execute.return_value = (201, 45)
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _check_rate_limit
			with self.assertRaises(Exception):
				_check_rate_limit("sess-001")

	def test_no_ttl_sets_expire(self):
		"""When key has no TTL (ttl == -1), expire() must be called."""
		m = self._run(count=1, ttl=-1)
		m.cache.return_value.expire.assert_called_once()

	def test_positive_ttl_does_not_set_expire(self):
		m = self._run(count=1, ttl=55)
		m.cache.return_value.expire.assert_not_called()

	def test_empty_session_id_skips_pipeline(self):
		frappe_mock = _make_frappe_mock()
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _check_rate_limit
			_check_rate_limit("")  # must not raise
		frappe_mock.cache.return_value.pipeline.assert_not_called()


# ─── Content-type → extension ────────────────────────────────────────────────

class TestExtFromContentType(unittest.TestCase):
	def _call(self, ct: str) -> str:
		from frappe_whatsapp_openwa.api.webhook import _ext_from_content_type
		return _ext_from_content_type(ct)

	def test_jpeg(self):           self.assertEqual(self._call("image/jpeg"), ".jpg")
	def test_png(self):            self.assertEqual(self._call("image/png"), ".png")
	def test_pdf(self):            self.assertEqual(self._call("application/pdf"), ".pdf")
	def test_mp4(self):            self.assertEqual(self._call("video/mp4"), ".mp4")
	def test_ogg(self):            self.assertEqual(self._call("audio/ogg"), ".ogg")
	def test_charset_stripped(self):
		self.assertEqual(self._call("image/png; charset=utf-8"), ".png")
	def test_unknown_returns_empty(self):
		self.assertEqual(self._call("application/octet-stream"), "")


# ─── ACK handler ─────────────────────────────────────────────────────────────

class TestHandleAck(unittest.TestCase):
	def test_read_ack_updates_status(self):
		frappe_mock = _make_frappe_mock()
		frappe_mock.db.get_value.return_value = "WA-MSG-001"
		log = MagicMock()
		payload = {
			"event": "message.ack",
			"sessionId": "sess-001",
			"data": {"id": {"id": "wamid.abc"}, "ack": 3},
		}
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _handle_ack
			_handle_ack(payload, log)
		frappe_mock.db.set_value.assert_called_once_with(
			"WhatsApp Message", "WA-MSG-001", "status", "Read"
		)

	def test_unknown_message_id_skips_set_value(self):
		frappe_mock = _make_frappe_mock()
		frappe_mock.db.get_value.return_value = None
		log = MagicMock()
		payload = {"event": "message.ack", "data": {"id": {"id": "nope"}, "ack": 1}}
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _handle_ack
			_handle_ack(payload, log)
		frappe_mock.db.set_value.assert_not_called()


# ─── Idempotency ─────────────────────────────────────────────────────────────

class TestIdempotency(unittest.TestCase):
	def test_existing_key_is_duplicate(self):
		frappe_mock = _make_frappe_mock()
		frappe_mock.cache.return_value.exists.return_value = True
		with patch("frappe_whatsapp_openwa.utils.idempotency.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.idempotency import is_duplicate
			self.assertTrue(is_duplicate("message", "wamid.abc"))

	def test_new_key_not_duplicate(self):
		frappe_mock = _make_frappe_mock()
		frappe_mock.cache.return_value.exists.return_value = False
		with patch("frappe_whatsapp_openwa.utils.idempotency.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.idempotency import is_duplicate
			self.assertFalse(is_duplicate("message", "wamid.new"))

	def test_empty_id_always_not_duplicate(self):
		frappe_mock = _make_frappe_mock()
		with patch("frappe_whatsapp_openwa.utils.idempotency.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.idempotency import is_duplicate
			self.assertFalse(is_duplicate("message", ""))
		frappe_mock.cache.return_value.exists.assert_not_called()

	def test_mark_processed_sets_key_with_ttl(self):
		frappe_mock = _make_frappe_mock()
		with patch("frappe_whatsapp_openwa.utils.idempotency.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.idempotency import mark_processed
			mark_processed("message", "wamid.x")
		frappe_mock.cache.return_value.setex.assert_called_once_with(
			"openwa:webhook:dedup:message:wamid.x", 86400, "1"
		)


if __name__ == "__main__":
	unittest.main()
