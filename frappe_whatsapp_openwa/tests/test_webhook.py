"""Integration-style tests for api/webhook.py and utils/idempotency.py.

All Frappe globals are patched — no live site needed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
import unittest
from unittest.mock import MagicMock, patch


# ─── Helpers ────────────────────────────────────────────────────────────────

def _sign(body: bytes, secret: str = "test-secret") -> str:
	return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload_bytes(event: str = "message.received", session_id: str = "sess-001", **extra) -> bytes:
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
	cache.get_value.return_value = None
	cache.exists.return_value = False
	# make_key prefixes db_name in production; identity here keeps assertions readable.
	cache.make_key = lambda key, **kw: key
	m.cache = cache

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
		# The secret belongs to the session now, not the gateway.
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock), \
			patch("frappe_whatsapp_openwa.api.webhook._session_secret", return_value=secret):
			from frappe_whatsapp_openwa.api.webhook import _parse_and_verify
			return _parse_and_verify(frappe_mock.get_single.return_value), frappe_mock

	def test_session_without_a_secret_is_refused(self):
		"""Fails closed: an unverifiable delivery is not accepted on trust."""
		body = _payload_bytes("session.status")
		frappe_mock = _make_frappe_mock()
		frappe_mock.request = MagicMock()
		frappe_mock.request.get_data.return_value = body
		frappe_mock.request.headers = {"X-OpenWA-Signature": _sign(body)}
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock), \
			patch("frappe_whatsapp_openwa.api.webhook._session_secret", return_value=""):
			from frappe_whatsapp_openwa.api.webhook import _parse_and_verify
			with self.assertRaises(Exception):
				_parse_and_verify(frappe_mock.get_single.return_value)

	def test_another_sessions_secret_does_not_verify(self):
		"""One leaked secret must not authenticate a different session."""
		body = _payload_bytes("session.status")
		signed_with_other = _sign(body, secret="a-different-sessions-secret")
		with self.assertRaises(Exception):
			self._call(body, signed_with_other, secret="this-sessions-secret")

	def test_valid_signature_returns_parsed_payload(self):
		body = _payload_bytes("session.authenticated")
		sig = _sign(body)
		result, _ = self._call(body, sig)
		self.assertEqual(result["event"], "session.authenticated")

	def test_sha256_prefixed_signature_accepted(self):
		"""The gateway sends X-OpenWA-Signature: sha256=<hex> — the prefix must be stripped."""
		body = _payload_bytes("session.status")
		sig = f"sha256={_sign(body)}"
		result, _ = self._call(body, sig)
		self.assertEqual(result["event"], "session.status")

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
		pipe = frappe_mock.cache.pipeline.return_value
		pipe.execute.return_value = (count, ttl)
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _check_rate_limit
			_check_rate_limit(session_id)
		return frappe_mock

	def test_count_1_does_not_raise(self):
		self._run(count=1, ttl=60)  # no assertion — must not throw

	def test_over_limit_raises(self):
		frappe_mock = _make_frappe_mock()
		pipe = frappe_mock.cache.pipeline.return_value
		pipe.execute.return_value = (201, 45)
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _check_rate_limit
			with self.assertRaises(Exception):
				_check_rate_limit("sess-001")

	def test_no_ttl_sets_expire(self):
		"""When key has no TTL (ttl == -1), expire() must be called."""
		m = self._run(count=1, ttl=-1)
		m.cache.expire.assert_called_once()

	def test_positive_ttl_does_not_set_expire(self):
		m = self._run(count=1, ttl=55)
		m.cache.expire.assert_not_called()

	def test_empty_session_id_skips_pipeline(self):
		frappe_mock = _make_frappe_mock()
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _check_rate_limit
			_check_rate_limit("")  # must not raise
		frappe_mock.cache.pipeline.assert_not_called()


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
		# _handle_ack now fetches the current status too, so it can refuse an ack
		# that would move the message backwards (see test_ack_ordering.py).
		frappe_mock.db.get_value.return_value = {"name": "WA-MSG-001", "status": "Sent"}
		log = MagicMock()
		payload = {
			"event": "message.ack",
			"sessionId": "sess-001",
			"data": {"id": "msg-uuid", "messageId": "wamid.abc", "status": "read", "ack": 3},
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
		payload = {
			"event": "message.ack",
			"data": {"id": "msg-uuid", "messageId": "nope", "status": "sent", "ack": 1},
		}
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock):
			from frappe_whatsapp_openwa.api.webhook import _handle_ack
			_handle_ack(payload, log)
		frappe_mock.db.set_value.assert_not_called()


# ─── Idempotency ─────────────────────────────────────────────────────────────

class TestIdempotency(unittest.TestCase):
	def test_claim_returns_false_when_key_exists(self):
		"""SET NX returns None when the key already exists — claim_event returns False."""
		frappe_mock = _make_frappe_mock()
		frappe_mock.cache.set.return_value = None  # key already present
		with patch("frappe_whatsapp_openwa.utils.idempotency.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.idempotency import claim_event
			self.assertFalse(claim_event("message", "wamid.abc"))

	def test_claim_returns_true_for_new_key(self):
		"""SET NX returns True when the key was freshly inserted."""
		frappe_mock = _make_frappe_mock()
		frappe_mock.cache.set.return_value = True  # key was set
		with patch("frappe_whatsapp_openwa.utils.idempotency.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.idempotency import claim_event
			self.assertTrue(claim_event("message", "wamid.new"))

	def test_empty_id_always_claimable(self):
		"""Empty event_id has no deduplication — claim_event always returns True."""
		frappe_mock = _make_frappe_mock()
		with patch("frappe_whatsapp_openwa.utils.idempotency.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.idempotency import claim_event
			self.assertTrue(claim_event("message", ""))
		frappe_mock.cache.set.assert_not_called()

	def test_claim_uses_set_nx_with_ttl(self):
		"""claim_event must call cache.set(key, '1', nx=True, ex=TTL) — single atomic call."""
		frappe_mock = _make_frappe_mock()
		frappe_mock.cache.set.return_value = True
		with patch("frappe_whatsapp_openwa.utils.idempotency.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.idempotency import claim_event
			claim_event("message", "wamid.x")
		frappe_mock.cache.set.assert_called_once_with(
			"openwa:webhook:dedup:message:wamid.x", "1", nx=True, ex=86400
		)


if __name__ == "__main__":
	unittest.main()


# ─── Replay and re-delivery ──────────────────────────────────────────────────

class TestFreshness(unittest.TestCase):
	"""The signed body carries an ISO-8601 dispatch time, so a captured request
	expires instead of staying valid forever."""

	def _stale(self, timestamp, window=900, now="2026-09-08 12:00:00"):
		frappe_mock = MagicMock()
		frappe_mock.utils.get_datetime = lambda v: datetime.fromisoformat(str(v).replace("Z", ""))
		frappe_mock.utils.now_datetime.return_value = datetime.fromisoformat(now)
		with patch("frappe_whatsapp_openwa.api.webhook.frappe", frappe_mock), \
			patch("frappe_whatsapp_openwa.api.webhook.limit", return_value=window):
			from frappe_whatsapp_openwa.api.webhook import _is_stale
			return _is_stale(timestamp)

	def test_recent_delivery_is_fresh(self):
		self.assertFalse(self._stale("2026-09-08 11:58:00"))

	def test_old_delivery_is_stale(self):
		"""A request captured yesterday must not still work today."""
		self.assertTrue(self._stale("2026-09-07 12:00:00"))

	def test_boundary_is_inclusive_of_the_window(self):
		self.assertFalse(self._stale("2026-09-08 11:45:00"))

	def test_clock_skew_ahead_is_not_stale(self):
		"""A gateway running slightly fast must not have its traffic rejected."""
		self.assertFalse(self._stale("2026-09-08 12:05:00"))

	def test_missing_timestamp_is_not_stale(self):
		"""An older gateway sends none; the signature still guards the delivery."""
		self.assertFalse(self._stale(None))
		self.assertFalse(self._stale(""))

	def test_unparseable_timestamp_is_not_stale(self):
		self.assertFalse(self._stale("not a date"))


class TestReleaseEvent(unittest.TestCase):
	"""A claim is taken before the work, so a failure has to give it back."""

	def test_release_deletes_the_claim(self):
		frappe_mock = MagicMock()
		frappe_mock.cache.make_key = lambda k, **kw: k
		with patch("frappe_whatsapp_openwa.utils.idempotency.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.idempotency import release_event
			release_event("delivery", "idem-1")
		frappe_mock.cache.delete_value.assert_called_once()

	def test_blank_key_is_a_no_op(self):
		frappe_mock = MagicMock()
		with patch("frappe_whatsapp_openwa.utils.idempotency.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.idempotency import release_event
			release_event("delivery", "")
		frappe_mock.cache.delete_value.assert_not_called()

	def test_cache_failure_never_breaks_the_caller(self):
		frappe_mock = MagicMock()
		frappe_mock.cache.make_key = lambda k, **kw: k
		frappe_mock.cache.delete_value.side_effect = Exception("redis down")
		with patch("frappe_whatsapp_openwa.utils.idempotency.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.idempotency import release_event
			release_event("delivery", "idem-1")  # must not raise
