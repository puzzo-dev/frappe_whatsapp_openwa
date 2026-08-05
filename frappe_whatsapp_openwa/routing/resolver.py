"""Provider resolver — pure function, no side effects beyond Redis reads.

routing_mode_override values (from WhatsApp Account Provider Extension):
  ""/"inherit"/"Account-level" → use default_provider
  "Message-level"              → respect requested_provider if allow_message_level_override=1
  "Hybrid"                     → OpenWA if healthy, else Meta

default_provider values: "Meta Cloud API" | "OpenWA"

Result is cached 5 s per (account, requested_provider, session_strategy) to avoid repeated DB hits.
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
	session_strategy: str | None = None,
) -> tuple[ProviderChoice, str | None]:
	"""Return (provider, session_name). session_name is None when provider == 'meta'.

	session_strategy controls which OpenWA Session is selected when multiple
	sessions are linked to the same WhatsApp account via linked_whatsapp_account:
	  - "first"  or None: pick the first Connected session (default)
	  - "round_robin": cycle through Connected sessions
	  - "least_loaded": pick the session with the lowest messages_sent_today
	"""
	cache_key = f"openwa:resolver:{account_name}:{requested_provider or ''}:{session_strategy or ''}"
	cached = frappe.cache().get(cache_key)
	if cached:
		raw = cached.decode() if isinstance(cached, bytes) else cached
		parts = raw.split("|", 1)
		return parts[0], parts[1] if len(parts) > 1 and parts[1] else None  # type: ignore[return-value]

	result = _resolve(account_name, requested_provider, session_strategy)
	frappe.cache().setex(cache_key, _RESOLVER_CACHE_TTL, f"{result[0]}|{result[1] or ''}")
	return result


def _resolve(
	account_name: str,
	requested_provider: str | None,
	session_strategy: str | None = None,
) -> tuple[ProviderChoice, str | None]:
	try:
		ext = frappe.get_doc("WhatsApp Account Provider Extension", account_name)
	except frappe.DoesNotExistError:
		return "meta", None

	override = (ext.routing_mode_override or "").strip()

	# ── Hybrid: OpenWA when healthy, else Meta ────────────────────────────
	if override == "Hybrid":
		provider, session_name = _resolve_openwa(ext, session_strategy)
		if provider == "openwa" and _session_is_healthy(session_name):
			return "openwa", session_name
		return "meta", None

	# ── Message-level override: active whenever the flag is set ─────────────
	# Works even without routing_mode_override="Message-level" so that callers
	# can pass provider= from code without requiring an explicit mode selection.
	if requested_provider and ext.allow_message_level_override:
		if requested_provider.lower() == "openwa":
			return _resolve_openwa(ext, session_strategy)
		return "meta", None

	# ── Account-level / inherit / empty: use default_provider ────────────
	default = (ext.default_provider or "Meta Cloud API").strip()
	if default == "OpenWA":
		return _resolve_openwa(ext, session_strategy)
	return "meta", None


def _resolve_openwa(
	ext,
	session_strategy: str | None = None,
) -> tuple[ProviderChoice, str | None]:
	"""Resolve an OpenWA session for the account.

	Priority:
	  1. ext.openwa_session (explicit single-session link on the extension)
	  2. Sessions linked via OpenWA Session.linked_whatsapp_account, filtered
	     to Connected status, selected by session_strategy:
	       - "first" or None: first Connected session
	       - "round_robin": cycle through Connected sessions via Redis counter
	       - "least_loaded": session with lowest messages_sent_today
	"""
	session_name = ext.openwa_session
	if session_name:
		return "openwa", session_name

	account = ext.linked_whatsapp_account
	if not account:
		frappe.log_error(
			title="OpenWA Resolver: no account linked on extension",
			message=f"Extension {ext.name} has no linked_whatsapp_account.",
		)
		return "meta", None

	sessions = frappe.get_all(
		"OpenWA Session",
		filters={"linked_whatsapp_account": account, "status": "Connected"},
		fields=["name", "messages_sent_today"],
	)
	if not sessions:
		frappe.log_error(
			title="OpenWA Resolver: no Connected session linked",
			message=f"Account {account} has no Connected OpenWA session — falling back to Meta.",
		)
		return "meta", None

	if len(sessions) == 1:
		return "openwa", sessions[0]["name"]

	strategy = (session_strategy or "first").strip().lower()

	if strategy == "round_robin":
		rr_key = f"openwa:rr:{account}"
		current = frappe.cache().get_value(rr_key) or 0
		idx = int(current) + 1
		frappe.cache().set_value(rr_key, idx)
		chosen = sessions[idx % len(sessions)]
		return "openwa", chosen["name"]

	if strategy == "least_loaded":
		chosen = min(sessions, key=lambda s: s.get("messages_sent_today") or 0)
		return "openwa", chosen["name"]

	# Default: first Connected session
	return "openwa", sessions[0]["name"]


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
