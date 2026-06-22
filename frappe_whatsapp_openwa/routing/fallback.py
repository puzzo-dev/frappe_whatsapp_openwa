"""Auto-fallback: OpenWA → Meta only (one direction, one attempt).

Every fallback is logged to WhatsApp Fallback Log regardless of outcome.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import frappe

from frappe_whatsapp_openwa.providers.base import SendResult


@dataclass
class FallbackContext:
	account: str
	recipient: str
	message_id: str | None
	attempted_provider: str
	failure_reason: str
	failure_detail: str


def should_fallback(ext, attempted_provider: str, error: Exception) -> bool:
	"""True iff auto-fallback is configured and the direction is valid (OpenWA→Meta)."""
	if attempted_provider != "openwa":
		return False
	if not ext.auto_fallback_provider:
		return False
	settings = frappe.get_single("OpenWA Gateway Settings")
	return bool(settings.auto_fallback_enabled)


def run_fallback(
	ctx: FallbackContext,
	fallback_fn,  # callable() -> SendResult
) -> SendResult:
	"""Execute the fallback and write a Fallback Log entry."""
	try:
		result = fallback_fn()
		succeeded = result.success
		fallback_provider = result.provider
		# If fallback executed on the real doc (meta_fallback_fn path),
		# the message_id is now available on the result.
		final_message_id = result.message_id or ctx.message_id
	except Exception as e:
		result = SendResult(
			success=False, provider="meta", message_id=None, raw_response={}, error=str(e),
		)
		succeeded = False
		fallback_provider = "meta"
		final_message_id = ctx.message_id

	_log_fallback(
		dataclasses.replace(ctx, message_id=final_message_id),
		fallback_provider,
		succeeded,
		result,
	)
	return result


def _log_fallback(
	ctx: FallbackContext,
	fallback_provider: str,
	succeeded: bool,
	result: SendResult,
) -> None:
	# Do NOT frappe.db.commit() here — this runs inside a before_insert transaction.
	# Committing mid-hook can create partial state that survives a later rollback.
	# The log record will be committed together with the outer transaction.
	try:
		log = frappe.new_doc("WhatsApp Fallback Log")
		log.whatsapp_account = ctx.account
		log.recipient_phone = ctx.recipient
		log.message_id = ctx.message_id or ""
		log.attempted_provider = ctx.attempted_provider
		log.fallback_provider = fallback_provider
		log.fallback_succeeded = 1 if succeeded else 0
		log.failure_reason = _classify_reason(ctx.failure_reason)
		log.failure_detail = ctx.failure_detail
		log.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title="WhatsApp Fallback Log write failed",
			message=frappe.get_traceback(),
		)


def _classify_reason(error_str: str) -> str:
	"""Map an error string to one of the WhatsApp Fallback Log.failure_reason Select values."""
	e = error_str.lower()
	if any(k in e for k in ("session", "disconnected", "banned")):
		return "Session disconnected"
	if any(k in e for k in ("timeout", "network error", "connection refused", "connection error")):
		return "Network timeout"
	if any(k in e for k in ("rate limit", "429", "too many")):
		return "Rate limited"
	if any(k in e for k in ("api error", "400", "401", "403", "404", "4xx")):
		return "API error"
	return "Other"
