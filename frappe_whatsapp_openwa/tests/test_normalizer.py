"""Unit tests for translators/webhook_normalizer.py — pure functions, no Frappe.

Payload shapes mirror the OpenWA gateway (rmyndharis/OpenWA) webhook events:
message.received carries the full message object; message.ack carries
{id, messageId, status, ack}.
"""

from __future__ import annotations

import pytest

from frappe_whatsapp_openwa.translators.webhook_normalizer import (
	normalize_ack_event,
	normalize_message_event,
)


def _msg_payload(
	event="message.received",
	msg_type="text",
	body="Hello",
	has_media=False,
	caption=None,
	from_wa="2348012345678@c.us",
	to_wa="2348099999999@c.us",
	push_name="Alice",
	is_group=False,
	author=None,
	msg_id="true_2348012345678@c.us_3EB0ABCDEF",
):
	data = {
		"id": msg_id,
		"body": body,
		"type": msg_type,
		"timestamp": 1700000000,
		"from": from_wa,
		"to": to_wa,
		"hasMedia": has_media,
		"isGroup": is_group,
		"kind": "group" if is_group else "individual",
		"contact": {"id": from_wa, "pushName": push_name},
	}
	if author:
		data["author"] = author
	if caption is not None or has_media:
		data["media"] = {"mimetype": "image/jpeg", "caption": caption} if caption else {"mimetype": "image/jpeg"}
	return {
		"event": event,
		"sessionId": "8f3c2b1a-9d4e-4c7a-8b2f-1e6d5a4c3b2a",
		"data": data,
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
		n = normalize_message_event(_msg_payload(event="message.sent"))
		assert n is None

	def test_image_message(self):
		n = normalize_message_event(_msg_payload(
			msg_type="image",
			body="",
			has_media=True,
			caption="Look!",
		))
		assert n is not None
		assert n["content_type"] == "image"
		assert n["message"] == "Look!"
		assert n["_has_media"] is True

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
		))
		assert n is not None
		assert n["content_type"] == "document"

	def test_voice_note(self):
		n = normalize_message_event(_msg_payload(msg_type="voice", has_media=True))
		assert n is not None
		assert n["content_type"] == "audio"

	def test_group_message_uses_author_as_sender(self):
		n = normalize_message_event(_msg_payload(
			is_group=True,
			from_wa="120363000000000000@g.us",
			author="2348012345678@c.us",
		))
		assert n is not None
		assert n["from"] == "+2348012345678"

	def test_lid_sender_resolves_via_sender_phone(self):
		payload = _msg_payload(from_wa="12345678@lid")
		payload["data"]["senderPhone"] = "2348012345678"
		n = normalize_message_event(payload)
		assert n is not None
		assert n["from"] == "+2348012345678"

	def test_unknown_event_type_returns_none(self):
		payload = {"event": "group.join", "data": {}}
		assert normalize_message_event(payload) is None

	def test_missing_data_returns_none(self):
		payload = {"event": "message.received"}
		assert normalize_message_event(payload) is None

	def test_profile_name_extracted(self):
		n = normalize_message_event(_msg_payload())
		assert n["profile_name"] == "Alice"

	def test_session_id_preserved(self):
		n = normalize_message_event(_msg_payload())
		assert n["_openwa_session_id"] == "8f3c2b1a-9d4e-4c7a-8b2f-1e6d5a4c3b2a"

	def test_message_id_extracted(self):
		n = normalize_message_event(_msg_payload())
		assert n["message_id"] == "true_2348012345678@c.us_3EB0ABCDEF"


class TestNormalizeAckEvent:
	def _ack_payload(self, status: str, msg_id="true_234@c.us_3EB0ABC"):
		return {
			"event": "message.ack",
			"sessionId": "8f3c2b1a-9d4e-4c7a-8b2f-1e6d5a4c3b2a",
			"data": {
				"id": "9f1c2e7a-2b3d-4c5e-8a91-0d1e2f3a4b5c",
				"messageId": msg_id,
				"status": status,
				"ack": {"pending": 0, "sent": 1, "delivered": 2, "read": 3, "failed": -1}[status],
			},
		}

	def test_read_ack(self):
		result = normalize_ack_event(self._ack_payload("read"))
		assert result is not None
		assert result["ack_status"] == "Read"
		assert result["message_id"] == "true_234@c.us_3EB0ABC"

	def test_delivered_ack(self):
		result = normalize_ack_event(self._ack_payload("delivered"))
		assert result["ack_status"] == "Delivered"

	def test_sent_ack(self):
		result = normalize_ack_event(self._ack_payload("sent"))
		assert result["ack_status"] == "Sent"

	def test_failed_event(self):
		payload = self._ack_payload("failed")
		payload["event"] = "message.failed"
		result = normalize_ack_event(payload)
		assert result["ack_status"] == "Failed"

	def test_legacy_ack_int_fallback(self):
		payload = {
			"event": "message.ack",
			"data": {"id": "msg-uuid", "messageId": "true_x@c.us_ABC", "ack": 3},
		}
		result = normalize_ack_event(payload)
		assert result["ack_status"] == "Read"

	def test_wrong_event_returns_none(self):
		payload = {"event": "message.received", "data": {"id": "x", "ack": 1}}
		assert normalize_ack_event(payload) is None
