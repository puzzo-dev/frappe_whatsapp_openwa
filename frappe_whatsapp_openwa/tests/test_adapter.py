"""Unit tests for OpenWAAdapter — HTTP calls mocked via httpx.MockTransport."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from frappe_whatsapp_openwa.providers.base import (
	OpenWAClientError,
	OpenWARateLimited,
	OpenWASessionDown,
)
from frappe_whatsapp_openwa.providers.openwa import OpenWAAdapter


def _make_response(status: int, body: dict) -> httpx.Response:
	return httpx.Response(status_code=status, json=body)


class MockTransport(httpx.BaseTransport):
	"""Configurable single-response mock transport."""

	def __init__(self, responses: list[tuple[int, dict]]):
		self._responses = list(responses)
		self.requests: list[httpx.Request] = []

	def handle_request(self, request: httpx.Request) -> httpx.Response:
		if not self._responses:
			raise AssertionError(f"Unexpected request to {request.url}")
		self.requests.append(request)
		code, body = self._responses.pop(0)
		return httpx.Response(
			status_code=code,
			headers={"content-type": "application/json"},
			content=json.dumps(body).encode(),
		)


def _adapter(responses: list[tuple[int, dict]]) -> tuple[OpenWAAdapter, MockTransport]:
	adapter = OpenWAAdapter(
		gateway_url="http://openwa.test",
		api_key="test-key",
		session_id="test-session",
	)
	transport = MockTransport(responses)
	adapter._client = httpx.Client(
		transport=transport,
		headers={
			"X-API-Key": "test-key",
			"Authorization": "Bearer test-key",
		},
	)
	return adapter, transport


class TestSendText:
	def test_success(self):
		a, t = _adapter([(200, {"messageId": "abc123", "timestamp": 1719312000})])
		result = a.send_text("+2348012345678", "Hello", account="Test Account")
		assert result.success is True
		assert result.provider == "openwa"
		assert result.message_id == "abc123"

	def test_uses_send_text_endpoint_with_chat_id(self):
		a, t = _adapter([(200, {"messageId": "abc123"})])
		a.send_text("+2348012345678", "Hello", account="Test Account")
		req = t.requests[0]
		assert str(req.url) == "http://openwa.test/api/sessions/test-session/messages/send-text"
		assert req.headers["X-API-Key"] == "test-key"
		body = json.loads(req.content)
		assert body == {"chatId": "2348012345678@c.us", "text": "Hello"}

	def test_rate_limited_raises(self):
		a, _ = _adapter([(429, {"error": "rate limited"})])
		# tenacity retries on NetworkError/TimeoutException only — 429 bubbles immediately
		with pytest.raises(OpenWARateLimited):
			a.send_text("+2348012345678", "Hello", account="Test Account")

	def test_server_error_raises_session_down(self):
		a, _ = _adapter([(503, {})])
		with pytest.raises(OpenWASessionDown):
			a.send_text("+2348012345678", "Hello", account="Test Account")

	def test_client_error_raises_openwa_client_error(self):
		a, _ = _adapter([(400, {"error": "bad request"})])
		with pytest.raises(OpenWAClientError):
			a.send_text("+2348012345678", "Hello", account="Test Account")

	def test_message_id_falls_back_to_id_field(self):
		a, _ = _adapter([(200, {"id": "msg_xyz"})])
		result = a.send_text("+2348012345678", "Hello", account="Test Account")
		assert result.message_id == "msg_xyz"


class TestSendMedia:
	def test_image_uses_send_image_endpoint(self):
		a, t = _adapter([(200, {"messageId": "img1"})])
		result = a.send_media(
			"+2348012345678",
			"https://example.com/img.png",
			"Caption",
			"image",
			"Test Account",
		)
		assert result.success is True
		assert result.provider == "openwa"
		req = t.requests[0]
		assert str(req.url) == "http://openwa.test/api/sessions/test-session/messages/send-image"
		body = json.loads(req.content)
		assert body == {
			"chatId": "2348012345678@c.us",
			"url": "https://example.com/img.png",
			"caption": "Caption",
		}

	def test_document_uses_send_document_endpoint(self):
		a, t = _adapter([(200, {"messageId": "doc1"})])
		result = a.send_media(
			"+2348012345678",
			"https://example.com/file.pdf",
			None,
			"document",
			"Test Account",
		)
		assert result.success is True
		req = t.requests[0]
		assert str(req.url) == "http://openwa.test/api/sessions/test-session/messages/send-document"


class TestGetSessionStatus:
	def test_connected(self):
		a, t = _adapter([(200, {"status": "ready", "phone": "2348012345678"})])
		status = a.get_session_status("test-session")
		assert status.status == "Connected"
		assert str(t.requests[0].url) == "http://openwa.test/api/sessions/test-session"

	def test_qr_required(self):
		a, _ = _adapter([(200, {"status": "qr_ready"})])
		status = a.get_session_status("test-session")
		assert status.status == "QR Required"

	def test_unknown_status_maps_to_failed(self):
		a, _ = _adapter([(200, {"status": "something_new"})])
		status = a.get_session_status("test-session")
		assert status.status == "Failed"
