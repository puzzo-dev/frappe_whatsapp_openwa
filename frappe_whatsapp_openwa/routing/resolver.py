"""Provider resolver — pure function, no side effects beyond Redis reads.

routing_mode_override values (from WhatsApp Account Provider Extension):
  ""/"inherit"/"Account-level" → use default_provider
  "Message-level"              → respect requested_provider if allow_message_level_override=1
  "Hybrid"                     → OpenWA if healthy, else Meta

default_provider values: "Meta Cloud API" | "OpenWA"

Result is cached 5 s per (account, requested_provider) to avoid repeated DB hits.
"""

from __future__ import annotations

from typing import Literal

import frappe

from frappe_whatsapp_openwa.utils.cache import get_cached_session_status

ProviderChoice = Literal["meta", "openwa"]
_RESOLVER_CACHE_TTL = 5

_META_MODES = {"", "inherit", "Account-level", "Meta Cloud API"}


def resolve_provider(
	account_name: str,
	requested_provider: str | None = None,
) -> tuple[ProviderChoice, str | None]:
	"""Return (provider, session_name). session_name is None when provider == 'meta'."""
	cache_key = f"openwa:resolver:{account_name}:{requested_provider or ''}"
	cached = frappe.cache().get(cache_key)
	if cached:
		raw = cached.decode() if isinstance(cached, bytes) else cached
		parts = raw.split("|", 1)
		return parts[0], parts[1] if len(parts) > 1 and parts[1] else None  # type: ignore[return-value]

	result = _resolve(account_name, requested_provider)
	frappe.cache().setex(cache_key, _RESOLVER_CACHE_TTL, f"{result[0]}|{result[1] or ''}")
	return result


def _resolve(
	account_name: str,
	requested_provider: str | None,
) -> tuple[ProviderChoice, str | None]:
	try:
		ext = frappe.get_doc("WhatsApp Account Provider Extension", account_name)
	except frappe.DoesNotExistError:
		return "meta", None

	override = (ext.routing_mode_override or "").strip()

	# ── Hybrid: OpenWA when healthy, else Meta ────────────────────────────
	if override == "Hybrid":
		provider, session_name = _resolve_openwa(ext)
		if provider == "openwa" and _session_is_healthy(session_name):
			return "openwa", session_name
		return "meta", None

	# ── Message-level override: active whenever the flag is set ─────────────
	# Works even without routing_mode_override="Message-level" so that callers
	# can pass provider= from code without requiring an explicit mode selection.
	if requested_provider and ext.allow_message_level_override:
		if requested_provider.lower() == "openwa":
			return _resolve_openwa(ext)
		return "meta", None

	# ── Account-level / inherit / empty: use default_provider ────────────
	default = (ext.default_provider or "Meta Cloud API").strip()
	if default == "OpenWA":
		return _resolve_openwa(ext)
	return "meta", None


def _resolve_openwa(ext) -> tuple[ProviderChoice, str | None]:
	session_name = ext.openwa_session
	if not session_name:
		frappe.log_error(
			title="OpenWA Resolver: no session linked",
			message=f"Account {ext.linked_whatsapp_account} has no OpenWA session — falling back to Meta.",
		)
		return "meta", None
	return "openwa", session_name


def _session_is_healthy(session_name: str | None) -> bool:
	if not session_name:
		return False
	cached = get_cached_session_status(session_name)
	if cached is not None:
		return cached == "Connected"
	try:
		doc = frappe.get_doc("OpenWA Session", session_name)
		return doc.status == "Connected"
	except Exception:
		return False
