"""Normalize raw OpenWA webhook payloads into a standard dict.

Pure function — no Frappe imports, no DB calls.
Output maps 1-to-1 onto WhatsApp Message doctype fields so callers
can do ``frappe.new_doc("WhatsApp Message").update(normalized).insert()``.

Gateway payload reference (rmyndharis/OpenWA):

message.received — data is the full message object:
  id (string waMessageId, e.g. "true_2348...@c.us_3EB0ABCD"), from, to, body,
  type (text|image|video|audio|voice|document|sticker|location|contact|call|
        revoked|masked|unknown), timestamp (epoch seconds), isGroup, kind,
  hasMedia, author (group sender), senderPhone, contact {id, name, pushName},
  media {mimetype, filename?, data?, omitted?, sizeBytes?}

message.ack / message.failed — data = {id, messageId, status, ack}
  status is canonical: pending|sent|delivered|read|failed
"""

from __future__ import annotations

_OPENWA_TO_CONTENT_TYPE: dict[str, str] = {
	"text": "text",
	"image": "image",
	"video": "video",
	"audio": "audio",
	"voice": "audio",
	"document": "document",
	"sticker": "image",
	"location": "location",
	"contact": "contact",
	"call": "text",
	"revoked": "text",
	"masked": "text",
	"unknown": "text",
}

_ACK_STATUS_MAP: dict[str, str] = {
	"pending": "Pending",
	"sent": "Sent",
	"delivered": "Delivered",
	"read": "Read",
	"failed": "Failed",
}

# Legacy integer ack values — used only when the canonical status is absent.
_LEGACY_ACK_MAP = {-1: "Failed", 0: "Pending", 1: "Sent", 2: "Delivered", 3: "Read", 4: "Read"}


def normalize_message_event(payload: dict) -> dict | None:
	"""Return a normalized dict, or None if the event should be skipped."""
	event = payload.get("event", "")
	if event != "message.received":
		# message.sent is the echo of our own outbound sends — already recorded.
		return None

	data = payload.get("data") or {}
	if not data:
		return None

	raw_id = str(data.get("id") or "")
	if not raw_id:
		return None

	is_group = bool(data.get("isGroup")) or data.get("kind") == "group"
	# In groups, `from` is the group JID — the human sender is `author`.
	sender_wa = (data.get("author") or "") if is_group else ""
	if not sender_wa:
		sender_wa = data.get("from") or ""
	recipient_wa = data.get("to") or ""

	# @lid senders resolve to a phone via senderPhone when available.
	if sender_wa.endswith("@lid") and data.get("senderPhone"):
		sender_wa = data["senderPhone"]

	msg_type = data.get("type") or "text"
	content_type = _OPENWA_TO_CONTENT_TYPE.get(msg_type, "text")

	body: str = data.get("body") or ""
	caption: str = ""
	media = data.get("media") or {}
	if isinstance(media, dict):
		caption = media.get("caption") or ""

	# When body is a data-URI (base64 blob), don't store it in `message`
	message_text = "" if body.startswith("data:") else (body or caption)

	contact = data.get("contact") or {}
	profile_name = contact.get("pushName") or contact.get("name") or data.get("pushName") or ""

	return {
		"type": "Incoming",
		"status": "Received",
		"from": _wa_to_phone(sender_wa),
		"to": _wa_to_phone(recipient_wa),
		"message": message_text,
		"message_id": raw_id,
		"content_type": content_type,
		"attach": None,
		"profile_name": profile_name,
		"conversation_id": data.get("chatId") or "",
		"_openwa_session_id": payload.get("sessionId", ""),
		"_raw_msg_type": msg_type,
		"_has_media": bool(data.get("hasMedia")),
	}


def normalize_ack_event(payload: dict) -> dict | None:
	"""Return {message_id, ack_status} for a message.ack/message.failed event, or None."""
	event = payload.get("event", "")
	if event not in ("message.ack", "message.failed"):
		return None

	data = payload.get("data") or {}
	message_id = str(data.get("messageId") or data.get("id") or "")
	if not message_id:
		return None

	status = data.get("status")
	if status:
		ack_status = _ACK_STATUS_MAP.get(str(status).lower(), "Sent")
	else:
		ack_status = _LEGACY_ACK_MAP.get(data.get("ack", -1), "Sent")

	return {"message_id": message_id, "ack_status": ack_status}


def _wa_to_phone(wa_id: str) -> str:
	"""Strip @c.us / @g.us → E.164-ish string (e.g. '2348012345678' → '+2348012345678')."""
	digits = wa_id.split("@")[0]
	if digits and not digits.startswith("+"):
		return f"+{digits}"
	return digits
