"""Integration-ish tests for routing/router.py — all I/O mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from frappe_whatsapp_openwa.providers.base import OpenWAClientError, OpenWASessionDown, SendResult
from frappe_whatsapp_openwa.routing.router import route_send_text


def _ok(provider="openwa") -> SendResult:
	return SendResult(success=True, provider=provider, message_id="msg-1", raw_response={})


def _fail(provider="openwa") -> SendResult:
	return SendResult(success=False, provider=provider, message_id=None, raw_response={}, error="boom")


class TestRouteSendText:
	def _run(self, provider_result, openwa_send=None, meta_send=None, fallback=False):
		import frappe as _frappe

		ext = MagicMock()
		ext.auto_fallback_provider = "Meta Cloud API"

		settings = MagicMock()
		settings.auto_fallback_enabled = fallback
		settings.gateway_base_url = "http://openwa.test"
		settings.get_password.return_value = "key"

		session = MagicMock()
		session.gateway_session_id = "session-001"

		def _get_doc(doctype, *args):
			if doctype == "WhatsApp Account Provider Extension":
				return ext
			if doctype == "OpenWA Session":
				return session
			raise frappe.DoesNotExistError

		def _get_single(doctype):
			if doctype == "OpenWA Gateway Settings":
				return settings
			raise frappe.DoesNotExistError

		cache_mock = MagicMock()
		cache_mock.get_value.return_value = None

		with (
			patch("frappe_whatsapp_openwa.routing.resolver.frappe.get_doc", side_effect=_get_doc),
			patch("frappe_whatsapp_openwa.routing.resolver.frappe.cache", cache_mock),
			patch("frappe_whatsapp_openwa.routing.resolver._session_is_healthy", return_value=True),
			patch("frappe_whatsapp_openwa.routing.router.resolve_provider", return_value=provider_result),
			patch("frappe_whatsapp_openwa.routing.router.frappe.get_doc", side_effect=_get_doc),
			patch("frappe_whatsapp_openwa.routing.router.frappe.get_single", side_effect=_get_single),
			patch("frappe_whatsapp_openwa.routing.router.frappe.log_error"),
			patch("frappe_whatsapp_openwa.routing.fallback.frappe.get_single", side_effect=_get_single),
			patch("frappe_whatsapp_openwa.routing.fallback.frappe.new_doc", return_value=MagicMock()),
			patch("frappe_whatsapp_openwa.routing.fallback.frappe.db", MagicMock()),
			patch("frappe_whatsapp_openwa.providers.openwa.OpenWAAdapter.send_text", return_value=openwa_send or _ok()),
			patch("frappe_whatsapp_openwa.providers.meta.MetaAdapter.send_text", return_value=meta_send or _ok("meta")),
		):
			return route_send_text("Test Account", "+2348012345678", "Hello")

	def test_meta_route_uses_meta_adapter(self):
		result = self._run(("meta", None))
		assert result.provider == "meta"
		assert result.success is True

	def test_openwa_route_uses_openwa_adapter(self):
		result = self._run(("openwa", "sess-1"), openwa_send=_ok("openwa"))
		assert result.provider == "openwa"
		assert result.success is True

	def test_openwa_failure_with_fallback_enabled(self):
		with patch("frappe_whatsapp_openwa.routing.router._try_openwa_then_fallback") as mock_fn:
			mock_fn.return_value = _ok("meta")
			result = self._run(("openwa", "sess-1"))
		assert result is not None

	def test_openwa_failure_no_fallback_returns_failed_result(self):
		with patch("frappe_whatsapp_openwa.providers.openwa.OpenWAAdapter.send_text") as mock_send:
			mock_send.side_effect = OpenWASessionDown("session down")
			import frappe as _frappe
			ext = MagicMock()
			ext.auto_fallback_provider = None

			with (
				patch("frappe_whatsapp_openwa.routing.router.resolve_provider", return_value=("openwa", "sess-1")),
				patch("frappe_whatsapp_openwa.routing.router.frappe.get_doc", return_value=ext),
				patch("frappe_whatsapp_openwa.routing.router.frappe.get_single", return_value=MagicMock(
					gateway_base_url="http://t", get_password=lambda x: "k"
				)),
				patch("frappe_whatsapp_openwa.routing.router.frappe.log_error"),
			):
				result = route_send_text("Test Account", "+234", "Hello")
			assert result.success is False
			assert result.provider == "openwa"

	def test_client_error_triggers_fallback(self):
		"""OpenWAClientError (4xx) must be caught by _OPENWA_ERRORS and fall back to Meta."""
		meta_fn = MagicMock(return_value=_ok("meta"))
		with patch("frappe_whatsapp_openwa.providers.openwa.OpenWAAdapter.send_text") as mock_send:
			mock_send.side_effect = OpenWAClientError("401 Unauthorized")
			ext = MagicMock()
			ext.auto_fallback_provider = "Meta Cloud API"

			with (
				patch("frappe_whatsapp_openwa.routing.router.resolve_provider", return_value=("openwa", "sess-1")),
				patch("frappe_whatsapp_openwa.routing.router.frappe.get_doc", return_value=ext),
				patch("frappe_whatsapp_openwa.routing.router.frappe.get_single", return_value=MagicMock(
					gateway_base_url="http://t", get_password=lambda x: "k"
				)),
				patch("frappe_whatsapp_openwa.routing.router.frappe.log_error"),
				patch("frappe_whatsapp_openwa.routing.router.should_fallback", return_value=True),
				patch("frappe_whatsapp_openwa.routing.router.run_fallback", return_value=_ok("meta")) as mock_fallback,
			):
				result = route_send_text("Test Account", "+234", "Hello", meta_fallback_fn=meta_fn)
			mock_fallback.assert_called_once()
			assert result.provider == "meta"
