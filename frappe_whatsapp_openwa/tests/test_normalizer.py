"""Unit tests for translators/webhook_normalizer.py — pure functions, no Frappe."""

from __future__ import annotations

import pytest

from frappe_whatsapp_openwa.translators.webhook_normalizer import (
	normalize_ack_event,
	normalize_message_event,
)


def _msg_payload(
	event="message",
	from_me=False,
	msg_type="chat",
	body="Hello",
	has_media=False,
	media_url=None,
	caption=None,
):
	return {
		"event": event,
		"sessionId": "tenant-2348099999999-0",
		"data": {
			"id": {
				"_serialized": f"false_2348012345678@c.us_ABCDEF",
				"fromMe": from_me,
				"remote": "2348012345678@c.us",
				"id": "ABCDEF",
			},
			"body": body,
			"type": msg_type,
			"timestamp": 1700000000,
			"from": "2348012345678@c.us",
			"to": "2348099999999@c.us",
			"notifyName": "Alice",
			"hasMedia": has_media,
			"mediaUrl": media_url,
			"caption": caption,
		},
	}


class TestNormalizeMessageEvent:
	def test_basic_text_message(self):
		n = normalize_message_event(_msg_payload(body="Hi there"))
		assert n is not None
		assert n["message"] == "Hi there"
		assert n["type"] == "Incoming"
		assert n["status"] == "Received"
		assert n["content_type"] == "text"
		assert n["from"] == "+2348012345678"
		assert n["to"] == "+2348099999999"

	def test_skips_own_outbound_echo(self):
		n = normalize_message_event(_msg_payload(from_me=True))
		assert n is None

	def test_image_message(self):
		n = normalize_message_event(_msg_payload(
			msg_type="image",
			body="",
			has_media=True,
			media_url="https://cdn.example.com/img.jpg",
			caption="Look!",
		))
		assert n is not None
		assert n["content_type"] == "image"
		assert n["attach"] == "https://cdn.example.com/img.jpg"
		assert n["message"] == "Look!"

	def test_base64_body_not_stored_as_message(self):
		n = normalize_message_event(_msg_payload(
			msg_type="image",
			body="data:image/jpeg;base64,/9j/abc123",
			has_media=True,
		))
		assert n is not None
		assert n["message"] == ""

	def test_document_message(self):
		n = normalize_message_event(_msg_payload(
			msg_type="document",
			has_media=True,
			media_url="https://cdn.example.com/file.pdf",
		))
		assert n is not None
		assert n["content_type"] == "document"

	def test_audio_ptt(self):
		n = normalize_message_event(_msg_payload(msg_type="ptt", has_media=True))
		assert n is not None
		assert n["content_type"] == "audio"

	def test_unknown_event_type_returns_none(self):
		payload = {"event": "other.event", "data": {}}
		assert normalize_message_event(payload) is None

	def test_missing_data_returns_none(self):
		payload = {"event": "message"}
		assert normalize_message_event(payload) is None

	def test_profile_name_extracted(self):
		n = normalize_message_event(_msg_payload())
		assert n["profile_name"] == "Alice"

	def test_session_id_preserved(self):
		n = normalize_message_event(_msg_payload())
		assert n["_openwa_session_id"] == "tenant-2348099999999-0"

	def test_message_id_extracted(self):
		n = normalize_message_event(_msg_payload())
		assert n["message_id"] == "ABCDEF"


class TestNormalizeAckEvent:
	def _ack_payload(self, ack_int: int, msg_id="MSG001"):
		return {
			"event": "message.ack",
			"sessionId": "tenant-phone-0",
			"data": {
				"id": {"id": msg_id, "_serialized": f"true_xxx@c.us_{msg_id}"},
				"ack": ack_int,
			},
		}

	def test_read_ack(self):
		result = normalize_ack_event(self._ack_payload(3))
		assert result is not None
		assert result["ack_status"] == "Read"
		assert result["message_id"] == "MSG001"

	def test_delivered_ack(self):
		result = normalize_ack_event(self._ack_payload(2))
		assert result["ack_status"] == "Delivered"

	def test_sent_ack(self):
		result = normalize_ack_event(self._ack_payload(1))
		assert result["ack_status"] == "Sent"

	def test_error_ack(self):
		result = normalize_ack_event(self._ack_payload(-1))
		assert result["ack_status"] == "Error"

	def test_wrong_event_returns_none(self):
		payload = {"event": "message", "data": {"id": {"id": "x"}, "ack": 1}}
		assert normalize_ack_event(payload) is None
