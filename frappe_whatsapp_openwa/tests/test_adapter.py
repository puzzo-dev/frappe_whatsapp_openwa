"""Unit tests for OpenWAAdapter — HTTP calls mocked via httpx.MockTransport."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from frappe_whatsapp_openwa.providers.base import (
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

	def handle_request(self, request: httpx.Request) -> httpx.Response:
		if not self._responses:
			raise AssertionError(f"Unexpected request to {request.url}")
		code, body = self._responses.pop(0)
		return httpx.Response(
			status_code=code,
			headers={"content-type": "application/json"},
			content=json.dumps(body).encode(),
		)


def _adapter(responses: list[tuple[int, dict]]) -> OpenWAAdapter:
	adapter = OpenWAAdapter(
		gateway_url="http://openwa.test",
		api_key="test-key",
		session_id="test-session",
	)
	adapter._client = httpx.Client(transport=MockTransport(responses))
	return adapter


class TestSendText:
	def test_success(self):
		a = _adapter([(200, {"messageId": "abc123"})])
		result = a.send_text("+2348012345678", "Hello", account="Test Account")
		assert result.success is True
		assert result.provider == "openwa"
		assert result.message_id == "abc123"

	def test_rate_limited_raises(self):
		a = _adapter([(429, {"error": "rate limited"})])
		# tenacity retries on NetworkError/TimeoutException only — 429 bubbles immediately
		with pytest.raises(OpenWARateLimited):
			a.send_text("+2348012345678", "Hello", account="Test Account")

	def test_server_error_raises_session_down(self):
		a = _adapter([(503, {})])
		with pytest.raises(OpenWASessionDown):
			a.send_text("+2348012345678", "Hello", account="Test Account")

	def test_client_error_raises_value_error(self):
		a = _adapter([(400, {"error": "bad request"})])
		with pytest.raises(ValueError):
			a.send_text("+2348012345678", "Hello", account="Test Account")

	def test_message_id_falls_back_to_id_field(self):
		a = _adapter([(200, {"id": "msg_xyz"})])
		result = a.send_text("+2348012345678", "Hello", account="Test Account")
		assert result.message_id == "msg_xyz"


class TestSendMedia:
	def test_image_uses_send_image_endpoint(self):
		a = _adapter([(200, {"messageId": "img1"})])
		result = a.send_media(
			"+2348012345678",
			"https://example.com/img.png",
			"Caption",
			"image",
			"Test Account",
		)
		assert result.success is True
		assert result.provider == "openwa"

	def test_document_uses_send_file_endpoint(self):
		a = _adapter([(200, {"messageId": "doc1"})])
		result = a.send_media(
			"+2348012345678",
			"https://example.com/file.pdf",
			None,
			"document",
			"Test Account",
		)
		assert result.success is True


class TestGetSessionStatus:
	def test_connected(self):
		a = _adapter([(200, {"status": "Connected", "qrCode": None})])
		status = a.get_session_status("test-session")
		assert status.status == "Connected"
		assert status.qr_code is None

	def test_qr_required(self):
		a = _adapter([(200, {"status": "QR Required", "qrCode": "data:image/png;base64,abc"})])
		status = a.get_session_status("test-session")
		assert status.status == "QR Required"
		assert status.qr_code is not None
