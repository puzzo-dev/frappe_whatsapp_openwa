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


# Session events carry no gateway event id and no timestamp, so a burst of
# identical requests cannot be told apart by content. The request signature is
# the only per-request identifier available, and it is stable for a given body,
# which makes it a usable replay token. The window is deliberately short: a
# genuine repeat of a byte-identical session event is a no-op for the state
# machine, but suppressing one for 24 h would hide real session flapping.
_SESSION_REPLAY_WINDOW = 60


def _session_replay_token() -> str:
	"""The signature of the current request — identical bodies replay identically."""
	try:
		return (frappe.request.headers.get("X-OpenWA-Signature") or "").strip()
	except Exception:
		return ""


def _handle_session(payload: dict, log) -> None:
	from frappe_whatsapp_openwa.monitoring.state import handle_session_event
	from frappe_whatsapp_openwa.utils.idempotency import claim_event

	# Defence in depth only. This bounds a replay burst; it cannot stop a
	# patient attacker replaying one captured request every window, because the
	# gateway signs the body alone and a captured request never expires. The
	# damage from that is contained in monitoring/self_healer.py, which
	# re-checks session state against the gateway before restarting. Closing
	# the hole properly needs the gateway to sign a timestamp or nonce.
	if not claim_event("session", _session_replay_token(), ttl=_SESSION_REPLAY_WINDOW):
		log.processed = 1
		log.error_message = "Duplicate session event ignored (replay window)"
		log.save(ignore_permissions=True)
		return

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

	# The raw gateway URL goes in first so the message is complete as soon as it
	# is saved; the background job below swaps it for the Frappe file URL once
	# the media has been copied across.
	if normalized.get("attach"):
		msg_doc.attach = normalized["attach"]

	msg_doc.insert(ignore_permissions=True)

	if normalized.get("attach"):
		# The download used to run inline, so the gateway waited on network and
		# disk — up to _MAX_MEDIA_BYTES — before it got its 200, and a gateway
		# that times out retries, turning one slow message into several.
		#
		# It also has to happen after the insert, not before: the File is
		# attached to the message, and before insert there is no name to attach
		# it to (see rehost_media_job).
		frappe.enqueue(
			"frappe_whatsapp_openwa.api.webhook.rehost_media_job",
			queue="short",
			timeout=300,
			enqueue_after_commit=True,
			job_id=f"openwa:rehost:{msg_doc.name}",
			deduplicate=True,
			message_name=msg_doc.name,
			media_url=normalized["attach"],
		)

	log.processed = 1
	log.whatsapp_message_doc = msg_doc.name
	log.save(ignore_permissions=True)


def _is_safe_media_url(url: str, settings=None) -> bool:
    """True if this bench may download *url*.

    The webhook body names the URL and the response is saved as a File the
    caller can read back, so an unguarded fetch is a full SSRF with
    exfiltration against anything reachable from the bench.

    The configured gateway host is allowed through even when it is private,
    because that is where legitimate media actually comes from — the gateway is
    normally deployed on localhost or a LAN address, and refusing it would
    silently stop media rehosting rather than fail safe.
    """
    from frappe_whatsapp_openwa.utils.urlguard import host_of, is_safe_fetch_url

    allowed = []
    if settings is not None:
        allowed.append(host_of(getattr(settings, "gateway_base_url", "")))
    return is_safe_fetch_url(url, allowed_hosts=allowed)


def _rehost_media(media_url: str, message_name: str, settings) -> str | None:
	"""Download media from the OpenWA gateway and save it as a private Frappe File.

	Returns the Frappe file URL on success, None on any failure (caller keeps
	the raw gateway URL as a fallback).

	`message_name` is the WhatsApp Message *document name*. It used to be the
	provider's message id, which is not the same thing — WhatsApp Message is
	hash-named — so every re-hosted file was attached to a document name that
	does not exist. Nothing rejected it (File only type-checks the reference),
	but the consequences were silent and total: the attachment never appeared on
	the message, deleting the message never cleaned the file up, and because
	permission on a private attachment is resolved through the document it hangs
	off, `has_permission` hit DoesNotExistError and returned False — so the
	media this function exists to make "viewable in-app" was viewable by nobody
	except its owner and the Administrator.

	Uses frappe.utils.file_manager.save_file() — the correct Frappe API for
	persisting raw bytes as a File document (frappe.get_doc with a `content`
	key does NOT reliably write the file content to disk in all versions).
	"""
	if not media_url or media_url.startswith("/files/"):
		return None

	if not _is_safe_media_url(media_url, settings):
		frappe.log_error(
			title="OpenWA media re-host refused: unsafe URL",
			message=f"Refused to fetch {media_url!r} for {message_name}",
		)
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
			# Off deliberately: _is_safe_media_url validated *this* host, and a
			# redirect would let a public URL send the fetch inward afterwards.
			follow_redirects=False,
		)

		# Stream the download so we can abort before exhausting memory/disk if
		# the gateway sends an oversized payload.
		with client.stream("GET", media_url) as resp:
			resp.raise_for_status()

			declared_length = resp.headers.get("content-length")
			if declared_length and int(declared_length) > _MAX_MEDIA_BYTES:
				frappe.log_error(
					title=f"OpenWA media re-host skipped: file too large for {message_name}",
					message=f"Content-Length: {declared_length} bytes (limit {_MAX_MEDIA_BYTES})",
				)
				return None

			chunks = []
			downloaded = 0
			for chunk in resp.iter_bytes(chunk_size=65_536):
				downloaded += len(chunk)
				if downloaded > _MAX_MEDIA_BYTES:
					frappe.log_error(
						title=f"OpenWA media re-host aborted: body exceeded limit for {message_name}",
						message=f"Downloaded {downloaded} bytes before abort (limit {_MAX_MEDIA_BYTES})",
					)
					return None
				chunks.append(chunk)
			content = b"".join(chunks)

		content_type = resp.headers.get("content-type", "application/octet-stream")
		ext = _ext_from_content_type(content_type)
		filename = f"openwa-inbound-{message_name}{ext}"

		file_doc = save_file(
			fname=filename,
			content=content,
			dt="WhatsApp Message",
			dn=message_name,
			is_private=1,
		)
		return file_doc.file_url
	except Exception as e:
		frappe.log_error(
			title=f"OpenWA media re-host failed for {message_name}",
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
		"WhatsApp Message", {"message_id": ack["message_id"]}, ["name", "status"], as_dict=True
	)
	if existing:
		# Delivery acks describe a one-way progression, but they can arrive out
		# of order, and an old one can also be replayed verbatim (the signature
		# covers the body alone). Applying them blindly let a stale "sent" land
		# after "read" and walk the status backwards, so a message the customer
		# had opened showed as merely sent. Only forward moves are applied;
		# Failed is not part of the progression and always applies.
		if _ack_supersedes(existing.get("status"), ack["ack_status"]):
			frappe.db.set_value("WhatsApp Message", existing["name"], "status", ack["ack_status"])
	else:
		# ACK for an unknown message_id — keep an audit trail instead of dropping it.
		log.error_message = f"ACK for unknown message_id {ack['message_id']}"

	log.processed = 1
	log.whatsapp_message_doc = existing["name"] if existing else ""
	log.save(ignore_permissions=True)


def _resolve_account_for_session(session_id: str) -> str | None:
	"""Find the WhatsApp Account an inbound message belongs to.

	Sessions link to their account through OpenWA Session.linked_whatsapp_account,
	which is what supports several sessions per account. This used to look only
	at WhatsApp Account Provider Extension.openwa_session — the single-link field
	kept for back-compat — so any account set up the current way resolved to
	nothing and its inbound messages were stored with no account at all.

	The current link is tried first; the legacy one remains as a fallback for
	accounts still configured that way.
	"""
	session = frappe.db.get_value(
		"OpenWA Session",
		{"gateway_session_id": session_id},
		["name", "linked_whatsapp_account"],
		as_dict=True,
	)
	if not session:
		session = frappe.db.get_value(
			"OpenWA Session", session_id, ["name", "linked_whatsapp_account"], as_dict=True
		)
	if not session:
		return None

	if session.get("linked_whatsapp_account"):
		return session["linked_whatsapp_account"]

	return frappe.db.get_value(
		"WhatsApp Account Provider Extension",
		{"openwa_session": session["name"]},
		"linked_whatsapp_account",
	)


# Delivery progression. Anything outside it (notably Failed) is not ordered and
# is always applied.
_ACK_RANK = {"Pending": 0, "Sent": 1, "Delivered": 2, "Read": 3}


def _ack_supersedes(current: str | None, incoming: str) -> bool:
	"""True if *incoming* is a later delivery state than *current*."""
	if incoming not in _ACK_RANK or current not in _ACK_RANK:
		return True
	return _ACK_RANK[incoming] > _ACK_RANK[current]


def rehost_media_job(message_name: str, media_url: str) -> None:
	"""Background half of inbound media re-hosting.

	Runs as whoever the webhook ran as, which is Guest — the endpoint is
	allow_guest. That is fine here: save_file inserts the File with
	ignore_permissions, and access to an attachment is decided by the document
	it is attached to rather than by the File's owner, so a user who can read
	the message can read its media.
	"""
	if not frappe.db.exists("WhatsApp Message", message_name):
		return

	# Re-delivery and job retries both land here; a message whose attachment is
	# already a Frappe file has nothing left to do.
	current = frappe.db.get_value("WhatsApp Message", message_name, "attach") or ""
	if current.startswith("/private/files/") or current.startswith("/files/"):
		return

	settings = frappe.get_single("OpenWA Gateway Settings")
	hosted_url = _rehost_media(media_url, message_name, settings)
	if hosted_url:
		frappe.db.set_value(
			"WhatsApp Message", message_name, "attach", hosted_url, update_modified=False
		)
