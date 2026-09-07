"""Outbound queue worker — runs every minute via scheduler_events."""

from __future__ import annotations

import frappe

from frappe_whatsapp_openwa.utils.session_cap import cap_reached, release_slot, reserve_slot

_STUCK_SENDING_MINUTES = 5
_BATCH_SIZE = 50

# Backoff delays per attempt number (in minutes). Capped at index -1 for attempts > len.
_BACKOFF_MINUTES = [0, 1, 3, 10, 30, 60]


def process_outbound_queue():
	"""Pick up Queued items whose session is healthy and retry with backoff."""
	from frappe_whatsapp_openwa.utils.cron import acquire_cron_lock, release_cron_lock

	if not acquire_cron_lock("outbound_queue", ttl_seconds=55):
		return  # Another worker instance is running

	try:
		_run_queue()
	finally:
		release_cron_lock("outbound_queue")


def _run_queue():
	now = frappe.utils.now_datetime()

	# ── Reaper: recover rows stuck in Sending for > N min ────────────────
	stale_cutoff = frappe.utils.add_to_date(now, minutes=-_STUCK_SENDING_MINUTES)
	# A row that already recorded a provider message id was delivered; only its
	# status write was lost. Re-queueing it would send the message a second
	# time, so it is excluded and left for an operator to inspect.
	#
	# This narrows the duplicate window but does not close it: a worker killed
	# between the gateway call and the status write leaves no message id, and
	# nothing in the row can distinguish that from a send that never left. The
	# gateway offers no idempotency key to make the retry safe, so this stays
	# at-least-once by construction — _STUCK_SENDING_MINUTES is set well beyond
	# any single request timeout so only a dead worker trips it.
	frappe.db.sql(
		"""UPDATE `tabWhatsApp Outbound Queue`
		   SET status = 'Queued',
		       failure_log = CONCAT(COALESCE(failure_log,''), ' [auto-recovered from stuck-Sending]')
		   WHERE status = 'Sending' AND last_attempt_at < %s
		     AND (resulting_message_id IS NULL OR resulting_message_id = '')""",
		stale_cutoff,
	)
	frappe.db.commit()

	# Only pick rows whose next_attempt_at is due (NULL rows are always due).
	queued = frappe.db.sql(
		"""SELECT name, account, recipient, message_type, requested_provider,
		          enqueued_at, max_age_minutes, attempts
		   FROM `tabWhatsApp Outbound Queue`
		   WHERE status = 'Queued'
		   AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
		   ORDER BY enqueued_at ASC
		   LIMIT %s""",
		[frappe.utils.now(), _BATCH_SIZE],
		as_dict=True,
	)
	if not queued:
		return

	for row in queued:
		try:
			_process_row(row, now)
		except Exception:
			frappe.log_error(
				title=f"Queue worker error for {row.name}",
				message=frappe.get_traceback(),
			)


def _process_row(row, now):
	# enqueued_at and max_age_minutes were already fetched in the initial batch SELECT.
	enqueued = frappe.utils.get_datetime(row.enqueued_at)
	max_age = row.max_age_minutes or 15
	if (now - enqueued).total_seconds() / 60 > max_age:
		frappe.db.set_value("WhatsApp Outbound Queue", row.name, "status", "Expired")
		frappe.db.commit()
		return

	from frappe_whatsapp_openwa.routing.resolver import resolve_provider
	from frappe_whatsapp_openwa.utils.cache import is_session_alive

	session_strategy = None
	payload = frappe.parse_json(
		frappe.db.get_value("WhatsApp Outbound Queue", row.name, "payload") or "{}"
	)
	if row.message_type == "template":
		tpl = (payload or {}).get("template")
		if tpl:
			session_strategy = frappe.db.get_value(
				"WhatsApp Templates", tpl, "custom_session_strategy"
			) or None

	try:
		provider, session_name = resolve_provider(row.account, row.requested_provider, session_strategy)
	except Exception:
		provider, session_name = "meta", None

	if provider == "openwa" and session_name and not is_session_alive(session_name):
		return  # Still unhealthy — leave Queued for next tick

	# ── Daily cap check (queue worker respects the cap too) ───────────────
	if provider == "openwa" and session_name and cap_reached(session_name):
		return  # Cap still active — leave Queued until midnight reset

	# ── Atomic claim ─────────────────────────────────────────────────────
	frappe.db.sql(
		"""UPDATE `tabWhatsApp Outbound Queue`
		   SET status = 'Sending',
		       attempts = COALESCE(attempts, 0) + 1,
		       last_attempt_at = %s
		   WHERE name = %s AND status = 'Queued'""",
		[frappe.utils.now(), row.name],
	)
	# ROW_COUNT() reflects the immediately preceding UPDATE in this connection —
	# no separate SELECT needed, and no race window between claim and verification.
	claimed = frappe.db.sql("SELECT ROW_COUNT()", as_list=True)[0][0]
	# This commit is load-bearing, not leftover debris: the claim only excludes
	# other workers once it is visible outside this transaction. Deferring it to
	# the end of the job would let two workers each read status='Queued', both
	# claim the row, and both send — the duplicate delivery this claim exists to
	# prevent.
	frappe.db.commit()

	if not claimed:
		return  # Another worker won the race

	attempts = (row.attempts or 0) + 1  # Incremented by the claim UPDATE above

	# Claim the session's cap slot before dispatching. The worker previously
	# checked the cap but never incremented the counter, so queued messages were
	# invisible to it: a backlog draining after a session recovered could blow
	# straight through the daily cap without ever registering a single send.
	reserved = False
	if provider == "openwa" and session_name:
		if not reserve_slot(session_name):
			# Someone took the last slot between the check above and here.
			frappe.db.set_value(
				"WhatsApp Outbound Queue", row.name, "status", "Queued",
				update_modified=False,
			)
			frappe.db.commit()
			return
		reserved = True

	try:
		result = _dispatch(row, payload, session_strategy)
		if result.success:
			frappe.db.set_value("WhatsApp Outbound Queue", row.name, {
				"status": "Sent",
				"final_provider_used": result.provider,
				"resulting_message_id": result.message_id or "",
			})
			# A Meta fallback consumes no OpenWA capacity.
			if reserved and result.provider != "openwa":
				release_slot(session_name)
		else:
			if reserved:
				release_slot(session_name)
			_handle_dispatch_failure(row.name, result.error or "Unknown error", attempts)
	except Exception as e:
		if reserved:
			release_slot(session_name)
		_handle_dispatch_failure(row.name, str(e), attempts)

	# Likewise required. The send above is an external side effect that cannot be
	# rolled back, so its outcome has to be durable before the next row is
	# processed; otherwise a later failure in this batch would roll back the
	# 'Sent' status of a message that really was delivered, and the next tick
	# would send it again.
	frappe.db.commit()


def _handle_dispatch_failure(doc_name: str, error: str, attempts: int) -> None:
	"""On failure: either schedule a retry with exponential backoff or mark Failed and create a dead-letter record."""
	max_attempts = len(_BACKOFF_MINUTES)
	if attempts < max_attempts:
		delay_minutes = _BACKOFF_MINUTES[min(attempts, len(_BACKOFF_MINUTES) - 1)]
		next_attempt = frappe.utils.add_to_date(frappe.utils.now(), minutes=delay_minutes)
		frappe.db.set_value("WhatsApp Outbound Queue", doc_name, {
			"status": "Queued",
			"next_attempt_at": next_attempt,
			"failure_log": error[:500],
		})
	else:
		frappe.db.set_value("WhatsApp Outbound Queue", doc_name, {
			"status": "Failed",
			"failure_log": error[:500],
		})
		_create_dead_letter(doc_name, error)


def _create_dead_letter(queue_name: str, error: str) -> None:
	"""Persist a dead-letter record for a permanently failed outbound message."""
	row = frappe.db.get_value(
		"WhatsApp Outbound Queue",
		queue_name,
		["account", "recipient", "message_type", "payload", "enqueued_at"],
		as_dict=True,
	)
	if not row:
		return
	try:
		frappe.get_doc({
			"doctype": "WhatsApp Fallback Log",
			"triggered_at": frappe.utils.now(),
			"whatsapp_account": row.account,
			"recipient_phone": row.recipient,
			"attempted_provider": row.message_type,
			"failure_reason": "Other",
			"failure_detail": f"Dead letter after exhausting retries. Error: {error[:2000]}\nPayload: {row.payload[:2000]}",
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title=f"Failed to create dead-letter for queue {queue_name}",
			message=frappe.get_traceback(),
		)


def _dispatch(row, payload: dict, session_strategy: str | None = None):
	from frappe_whatsapp_openwa.routing.router import route_send_media, route_send_text

	msg_type = row.message_type or "text"
	if msg_type == "text":
		return route_send_text(
			account_name=row.account,
			to=row.recipient,
			body=payload.get("body", ""),
			requested_provider=row.requested_provider,
			session_strategy=session_strategy,
			# This function is already the retry loop — see route_send_text.
			adapter_retries=False,
		)
	elif msg_type in ("image", "document", "video", "audio"):
		# _enqueue_for_later stores the media URL under "attach" and the caption
		# under "body". Reading only "media_url"/"caption" meant every queued
		# media message dispatched with an empty URL and no caption. Both key
		# spellings are accepted so rows already sitting in the queue drain
		# correctly instead of failing their way to Failed.
		return route_send_media(
			account_name=row.account,
			to=row.recipient,
			media_url=payload.get("media_url") or payload.get("attach") or "",
			caption=payload.get("caption") or payload.get("body") or None,
			media_type=msg_type,
			requested_provider=row.requested_provider,
			session_strategy=session_strategy,
			adapter_retries=False,
		)
	elif msg_type == "template":
		# _enqueue_for_later queues templates as "template", which had no branch
		# here at all: every template queued because a session was unhealthy
		# retried six times and died as "Unsupported message_type 'template'".
		# The template is flattened the same way the direct send path flattens
		# it, so a queued template and an immediate one produce the same text.
		return _dispatch_template(row, payload, session_strategy)
	else:
		from frappe_whatsapp_openwa.providers.base import SendResult
		return SendResult(
			success=False, provider="unknown", message_id=None,
			raw_response={}, error=f"Unsupported message_type '{msg_type}'"
		)


def _dispatch_template(row, payload: dict, session_strategy: str | None = None):
	"""Flatten a queued template and send it as text via the router."""
	from frappe_whatsapp_openwa.providers.base import SendResult
	from frappe_whatsapp_openwa.routing.router import route_send_text
	from frappe_whatsapp_openwa.translators.template_flattener import (
		extract_params_from_body_param,
		flatten_template,
	)

	template_name = payload.get("template") or ""
	if not template_name:
		return SendResult(
			success=False, provider="unknown", message_id=None,
			raw_response={}, error="Queued template row carries no template name",
		)

	try:
		template = frappe.get_doc("WhatsApp Templates", template_name)
	except frappe.DoesNotExistError:
		return SendResult(
			success=False, provider="unknown", message_id=None,
			raw_response={}, error=f"Template '{template_name}' no longer exists",
		)

	# Shared with the direct send path so the two cannot drift apart again.
	from frappe_whatsapp_openwa.overrides.whatsapp_message import _extract_button_labels

	body = flatten_template(
		body=template.template or "",
		parameters=extract_params_from_body_param(payload.get("body_param") or ""),
		header=template.header or None,
		footer=template.footer or None,
		buttons=_extract_button_labels(template) or None,
	)

	return route_send_text(
		account_name=row.account,
		to=row.recipient,
		body=body,
		requested_provider=row.requested_provider,
		session_strategy=session_strategy,
		adapter_retries=False,
	)
