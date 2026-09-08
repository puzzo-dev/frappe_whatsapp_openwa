"""Router — combines resolver + adapter construction + fallback orchestration."""

from __future__ import annotations

from typing import Callable, Literal

import frappe

from frappe_whatsapp_openwa.providers.base import (
	OpenWAClientError,
	OpenWANetworkError,
	OpenWARateLimited,
	OpenWASessionDown,
	SendResult,
)
from frappe_whatsapp_openwa.providers.meta import MetaAdapter
from frappe_whatsapp_openwa.providers.openwa import OpenWAAdapter
from frappe_whatsapp_openwa.routing.fallback import FallbackContext, run_fallback, should_fallback
from frappe_whatsapp_openwa.routing.resolver import resolve_provider

_OPENWA_ERRORS = (OpenWASessionDown, OpenWARateLimited, OpenWANetworkError, OpenWAClientError)


def route_send_text(
	account_name: str,
	to: str,
	body: str,
	requested_provider: str | None = None,
	meta_fallback_fn: Callable[[], SendResult] | None = None,
	session_strategy: str | None = None,
	adapter_retries: bool = True,
	session_override: str | None = None,
) -> SendResult:
	"""Route a text message.

	meta_fallback_fn — optional override for the Meta fallback callable.
	  When called from the doctype override, pass a closure that calls
	  super().send_outgoing() on the real doc instead of creating a throwaway.
	  When called from the queue worker, leave None to use MetaAdapter.

	adapter_retries — False from the queue worker, which retries the whole
	  message itself; leaving the adapter's own retry on as well multiplies one
	  network blip into many gateway calls, and a timeout is not proof of
	  non-delivery. Passed explicitly rather than through frappe.flags so it
	  cannot leak between jobs sharing a worker process.

	session_override — a session the caller has already resolved. The doctype
	  override resolves before sending so it can claim that session's cap slot;
	  resolving a second time here could pick a different session under a
	  spreading routing mode, and the message would then go out on a session
	  whose slot nobody claimed while the claimed one leaked a slot.
	"""
	if session_override:
		provider, session_name = "openwa", session_override
	else:
		provider, session_name = resolve_provider(account_name, requested_provider, session_strategy)
	_meta = meta_fallback_fn or (lambda: MetaAdapter(account_name).send_text(to, body, account_name))
	if provider == "meta":
		return _meta()
	return _try_openwa_then_fallback(
		account_name, session_name, to,
		fn=lambda adapter: adapter.send_text(to, body, account_name),
		fallback_fn=_meta,
		adapter_retries=adapter_retries,
	)


def route_send_media(
	account_name: str,
	to: str,
	media_url: str,
	caption: str | None,
	media_type: Literal["image", "document", "video", "audio"],
	requested_provider: str | None = None,
	meta_fallback_fn: Callable[[], SendResult] | None = None,
	session_strategy: str | None = None,
	adapter_retries: bool = True,
	session_override: str | None = None,
) -> SendResult:
	_meta = meta_fallback_fn or (
		lambda: MetaAdapter(account_name).send_media(to, media_url, caption, media_type, account_name)
	)
	if session_override:
		provider, session_name = "openwa", session_override
	else:
		provider, session_name = resolve_provider(account_name, requested_provider, session_strategy)
	if provider == "meta":
		return _meta()
	return _try_openwa_then_fallback(
		account_name, session_name, to,
		fn=lambda adapter: adapter.send_media(to, media_url, caption, media_type, account_name),
		fallback_fn=_meta,
		adapter_retries=adapter_retries,
	)


def _try_openwa_then_fallback(
	account_name: str,
	session_name: str | None,
	recipient: str,
	fn: Callable[[OpenWAAdapter], SendResult],
	fallback_fn: Callable[[], SendResult],
	adapter_retries: bool = True,
) -> SendResult:
	adapter = _build_openwa_adapter(session_name, adapter_retries)
	if adapter is None:
		return fallback_fn()

	try:
		return fn(adapter)
	except _OPENWA_ERRORS as exc:
		failure = str(exc)
		try:
			ext = frappe.get_doc("WhatsApp Account Provider Extension", account_name)
		except Exception:
			ext = None

		if ext and should_fallback(ext, "openwa", exc):
			ctx = FallbackContext(
				account=account_name,
				recipient=recipient,
				message_id=None,
				attempted_provider="openwa",
				failure_reason=type(exc).__name__,
				failure_detail=failure,
			)
			return run_fallback(ctx, fallback_fn)

		return SendResult(
			success=False, provider="openwa", message_id=None, raw_response={}, error=failure,
		)
	except Exception as exc:
		# Unexpected error (programming bug etc.) — do NOT silently fall back to Meta.
		# Log it and return a failed result so the caller can decide.
		frappe.log_error(
			title="OpenWA router unexpected error",
			message=frappe.get_traceback(),
		)
		return SendResult(
			success=False, provider="openwa", message_id=None, raw_response={}, error=str(exc),
		)


def _build_openwa_adapter(
	session_name: str | None, adapter_retries: bool = True
) -> OpenWAAdapter | None:
	if not session_name:
		return None
	try:
		settings = frappe.get_single("OpenWA Gateway Settings")
		session = frappe.get_doc("OpenWA Session", session_name)
		return OpenWAAdapter(
			gateway_url=settings.gateway_base_url,
			api_key=settings.get_password("gateway_api_key"),
			session_id=session.gateway_session_id or session_name,
			retry_network_errors=adapter_retries,
		)
	except Exception as e:
		frappe.log_error(title="OpenWA adapter build failed", message=str(e))
		return None
