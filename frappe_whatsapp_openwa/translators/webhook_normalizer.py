"""Normalize raw OpenWA webhook payloads into a standard dict.

Pure function — no Frappe imports, no DB calls.
Output maps 1-to-1 onto WhatsApp Message doctype fields so callers
can do ``frappe.new_doc("WhatsApp Message").update(normalized).insert()``.
"""

from __future__ import annotations

from typing import Literal

_OPENWA_TO_CONTENT_TYPE: dict[str, str] = {
	"chat": "text",
	"image": "image",
	"video": "video",
	"audio": "audio",
	"ptt": "audio",
	"document": "document",
	"sticker": "image",
	"location": "text",
	"vcard": "text",
	"revoked": "text",
}


def normalize_message_event(payload: dict) -> dict | None:
	"""Return a normalized dict, or None if the event should be skipped."""
	event = payload.get("event", "")
	if event not in ("message", "message_create"):
		return None

	data = payload.get("data") or payload.get("message") or {}
	if not data:
		return None

	msg_id_obj = data.get("id") or {}
	from_me: bool = msg_id_obj.get("fromMe", False) if isinstance(msg_id_obj, dict) else False

	# Skip echo of our own outbound messages (OpenWA mirrors them back)
	if from_me and event == "message":
		return None

	serialized_id: str = (
		msg_id_obj.get("_serialized", "") if isinstance(msg_id_obj, dict) else str(msg_id_obj)
	)
	raw_id: str = (
		msg_id_obj.get("id", serialized_id) if isinstance(msg_id_obj, dict) else serialized_id
	)

	sender_wa: str = data.get("from", "")
	recipient_wa: str = data.get("to", "")

	sender_phone = _wa_to_phone(sender_wa)
	recipient_phone = _wa_to_phone(recipient_wa)

	msg_type = data.get("type", "chat")
	content_type = _OPENWA_TO_CONTENT_TYPE.get(msg_type, "text")

	body: str = data.get("body") or ""
	caption: str = data.get("caption") or ""
	has_media: bool = bool(data.get("hasMedia") or data.get("mediaUrl"))
	media_url: str = data.get("mediaUrl") or (body if has_media and body.startswith("http") else "")

	# When body is a data-URI (base64 blob), don't store it in `message`
	message_text = "" if body.startswith("data:") else (body or caption)

	return {
		"type": "Incoming",
		"status": "Received",
		"from": sender_phone,
		"to": recipient_phone,
		"message": message_text,
		"message_id": raw_id,
		"content_type": content_type,
		"attach": media_url or None,
		"profile_name": data.get("notifyName") or data.get("pushName") or "",
		"conversation_id": serialized_id,
		"_openwa_session_id": payload.get("sessionId", ""),
		"_raw_msg_type": msg_type,
	}


def normalize_ack_event(payload: dict) -> dict | None:
	"""Return {message_id, ack_status} for a message.ack event, or None."""
	event = payload.get("event", "")
	if event not in ("message.ack", "ack"):
		return None

	data = payload.get("data") or {}
	msg_id_obj = data.get("id") or {}
	raw_id = msg_id_obj.get("id", "") if isinstance(msg_id_obj, dict) else str(msg_id_obj)

	ack_int = data.get("ack", -1)
	ack_map = {-1: "Error", 0: "Pending", 1: "Sent", 2: "Delivered", 3: "Read", 4: "Played"}
	ack_status = ack_map.get(ack_int, "Sent")

	return {"message_id": raw_id, "ack_status": ack_status}


def _wa_to_phone(wa_id: str) -> str:
	"""Strip @c.us / @g.us → E.164-ish string (e.g. '2348012345678' → '+2348012345678')."""
	digits = wa_id.split("@")[0]
	if digits and not digits.startswith("+"):
		return f"+{digits}"
	return digits
