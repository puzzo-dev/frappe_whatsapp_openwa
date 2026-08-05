import hashlib
import hmac

import frappe

# Rate limit: max requests per window per gateway session_id (identified after HMAC).
_RATE_LIMIT_WINDOW = 60   # seconds
_RATE_LIMIT_MAX = 200     # events per window per session

# Maximum media file size re-hosted from the gateway into Frappe File storage.
_MAX_MEDIA_BYTES = 50 * 1024 * 1024  # 50 MB


@frappe.whitelist(allow_guest=True)
def receive():
	"""Inbound webhook endpoint: POST /api/method/frappe_whatsapp_openwa.api.webhook.receive

	Handles:
	  - HMAC-SHA256 signature verification (mandatory — fails closed if unconfigured)
	  - Per-session rate limiting (200 events / 60 s)
	  - session.* events  → monitoring/state.handle_session_event
	  - message events    → create/update WhatsApp Message via normalizer
	  - message.ack       → update WhatsApp Message status
	  - Redis idempotency (24h TTL) per message_id
	"""
	settings = frappe.get_single("OpenWA Gateway Settings")
	payload = _parse_and_verify(settings)

	event = payload.get("event", "")
	session_id = payload.get("sessionId", "")

	_check_rate_limit(session_id)

	log = frappe.get_doc({
		"doctype": "OpenWA Webhook Log",
		"received_at": frappe.utils.now(),
		"session_id": session_id,
		"event_type": event,
		"raw_payload": frappe.as_json(payload),
	}).insert(ignore_permissions=True)

	try:
		if event.startswith("session."):
			_handle_session(payload, log)
		elif event == "message.received":
			_handle_inbound_message(payload, log, settings)
		elif event in ("message.ack", "message.failed"):
			_handle_ack(payload, log)
		elif event in ("ping", "test"):
			return {"status": "ok", "message": "pong"}
	except Exception:
		frappe.log_error(
			title=f"OpenWA webhook handler error [{event}]",
			message=frappe.get_traceback(),
		)
		log.error_message = frappe.get_traceback()[:1000]
		log.save(ignore_permissions=True)

	return {"status": "ok"}


def _parse_and_verify(settings) -> dict:
	secret = (settings.get_password("webhook_secret") or "").strip()
	if not secret:
		frappe.throw(
			"Webhook secret is not configured. Set it in OpenWA Gateway Settings.",
			frappe.AuthenticationError,
		)
	body = frappe.request.get_data()
	# Gateway sends: X-OpenWA-Signature: sha256=<hex>
	signature = (frappe.request.headers.get("X-OpenWA-Signature") or "").strip()
	if signature.startswith("sha256="):
		signature = signature[len("sha256="):]
	expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
	if not hmac.compare_digest(signature, expected):
		frappe.throw("Invalid webhook signature", frappe.AuthenticationError)
	return frappe.parse_json(body)


def _check_rate_limit(session_id: str) -> None:
	"""Reject bursts above _RATE_LIMIT_MAX events per _RATE_LIMIT_WINDOW seconds per session.

	Uses a Redis pipeline so the INCR and the conditional EXPIRE are issued
	back-to-back without a TOCTOU gap between them.
	"""
	if not session_id:
		return
	# make_key prefixes the site's db_name so the counter is tenant-scoped.
	key = frappe.cache.make_key(f"openwa:webhook:ratelimit:{session_id}")
	pipe = frappe.cache.pipeline()
	pipe.incr(key)
	pipe.ttl(key)
	count, ttl = pipe.execute()
	if ttl < 0:
		# Key exists but has no expiry (first call or expiry lost) — set it now.
		frappe.cache.expire(key, _RATE_LIMIT_WINDOW)
	if count > _RATE_LIMIT_MAX:
		frappe.throw(
			f"Rate limit exceeded for session {session_id}",
			frappe.TooManyRequestsError,
		)


def _handle_session(payload: dict, log) -> None:
	from frappe_whatsapp_openwa.monitoring.state import handle_session_event
	handle_session_event(payload)
	log.processed = 1
	log.save(ignore_permissions=True)


def _handle_inbound_message(payload: dict, log, settings) -> None:
	from frappe_whatsapp_openwa.translators.webhook_normalizer import normalize_message_event
	from frappe_whatsapp_openwa.utils.idempotency import claim_event

	normalized = normalize_message_event(payload)
	if normalized is None:
		log.processed = 1
		log.save(ignore_permissions=True)
		return

	message_id = normalized.get("message_id", "")
	if not claim_event("message", message_id):
		log.processed = 1
		log.error_message = "duplicate"
		log.save(ignore_permissions=True)
		return

	account_name = _resolve_account_for_session(payload.get("sessionId", ""))
	if not account_name:
		log.error_message = f"No account found for session {payload.get('sessionId')}"
		log.save(ignore_permissions=True)
		return

	msg_doc = frappe.new_doc("WhatsApp Message")
	msg_doc.update({
		"type": normalized["type"],
		"status": normalized["status"],
		"from": normalized["from"],
		"to": normalized["to"],
		"message": normalized["message"],
		"message_id": message_id,
		"content_type": normalized["content_type"],
		"profile_name": normalized["profile_name"],
		"whatsapp_account": account_name,
	})

	# Re-host media from the gateway to Frappe's File storage so the attachment
	# is viewable in-app without requiring gateway credentials.
	if normalized.get("attach"):
		hosted_url = _rehost_media(normalized["attach"], message_id, settings)
		msg_doc.attach = hosted_url or normalized["attach"]

	msg_doc.insert(ignore_permissions=True)

	log.processed = 1
	log.whatsapp_message_doc = msg_doc.name
	log.save(ignore_permissions=True)


def _rehost_media(media_url: str, message_id: str, settings) -> str | None:
	"""Download media from the OpenWA gateway and save it as a private Frappe File.

	Returns the Frappe file URL on success, None on any failure (caller keeps
	the raw gateway URL as a fallback).

	Uses frappe.utils.file_manager.save_file() — the correct Frappe API for
	persisting raw bytes as a File document (frappe.get_doc with a `content`
	key does NOT reliably write the file content to disk in all versions).
	"""
	if not media_url or media_url.startswith("/files/"):
		return None
	try:
		import httpx
		from frappe.utils.file_manager import save_file

		api_key = settings.get_password("gateway_api_key") or ""
		client = httpx.Client(
			headers={
				"X-API-Key": api_key,
				"Authorization": f"Bearer {api_key}",
			},
			timeout=15.0,
			follow_redirects=True,
		)

		# Stream the download so we can abort before exhausting memory/disk if
		# the gateway sends an oversized payload.
		with client.stream("GET", media_url) as resp:
			resp.raise_for_status()

			declared_length = resp.headers.get("content-length")
			if declared_length and int(declared_length) > _MAX_MEDIA_BYTES:
				frappe.log_error(
					title=f"OpenWA media re-host skipped: file too large for {message_id}",
					message=f"Content-Length: {declared_length} bytes (limit {_MAX_MEDIA_BYTES})",
				)
				return None

			chunks = []
			downloaded = 0
			for chunk in resp.iter_bytes(chunk_size=65_536):
				downloaded += len(chunk)
				if downloaded > _MAX_MEDIA_BYTES:
					frappe.log_error(
						title=f"OpenWA media re-host aborted: body exceeded limit for {message_id}",
						message=f"Downloaded {downloaded} bytes before abort (limit {_MAX_MEDIA_BYTES})",
					)
					return None
				chunks.append(chunk)
			content = b"".join(chunks)

		content_type = resp.headers.get("content-type", "application/octet-stream")
		ext = _ext_from_content_type(content_type)
		filename = f"openwa-inbound-{message_id}{ext}"

		file_doc = save_file(
			fname=filename,
			content=content,
			dt="WhatsApp Message",
			dn=message_id,
			is_private=1,
		)
		return file_doc.file_url
	except Exception as e:
		frappe.log_error(
			title=f"OpenWA media re-host failed for {message_id}",
			message=str(e),
		)
		return None


def _ext_from_content_type(ct: str) -> str:
	mapping = {
		"image/jpeg": ".jpg",
		"image/png": ".png",
		"image/webp": ".webp",
		"image/gif": ".gif",
		"video/mp4": ".mp4",
		"audio/ogg": ".ogg",
		"audio/mpeg": ".mp3",
		"application/pdf": ".pdf",
	}
	base = ct.split(";")[0].strip()
	return mapping.get(base, "")


def _handle_ack(payload: dict, log) -> None:
	from frappe_whatsapp_openwa.translators.webhook_normalizer import normalize_ack_event

	ack = normalize_ack_event(payload)
	if not ack or not ack.get("message_id"):
		log.processed = 1
		log.save(ignore_permissions=True)
		return

	existing = frappe.db.get_value(
		"WhatsApp Message", {"message_id": ack["message_id"]}, "name"
	)
	if existing:
		frappe.db.set_value("WhatsApp Message", existing, "status", ack["ack_status"])
	else:
		# ACK for an unknown message_id — keep an audit trail instead of dropping it.
		log.error_message = f"ACK for unknown message_id {ack['message_id']}"

	log.processed = 1
	log.whatsapp_message_doc = existing or ""
	log.save(ignore_permissions=True)


def _resolve_account_for_session(session_id: str) -> str | None:
	session_name = frappe.db.get_value(
		"OpenWA Session", {"gateway_session_id": session_id}, "name"
	)
	if not session_name:
		session_name = frappe.db.get_value("OpenWA Session", session_id, "name")
	if not session_name:
		return None
	return frappe.db.get_value(
		"WhatsApp Account Provider Extension",
		{"openwa_session": session_name},
		"linked_whatsapp_account",
	)
