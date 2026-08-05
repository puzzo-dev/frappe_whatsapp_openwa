"""Provider resolver — pure function, no side effects beyond Redis reads.

routing_mode_override values (from WhatsApp Account Provider Extension):
  ""/"inherit"/"Account-level" → use default_provider
  "Message-level"              → respect requested_provider if allow_message_level_override=1
  "Hybrid"                     → OpenWA if healthy, else Meta

default_provider values: "Meta Cloud API" | "OpenWA"

Result is cached 5 s per (account, requested_provider) to avoid repeated DB hits.
"""

from __future__ import annotations

import random
from typing import Literal

import frappe

from frappe_whatsapp_openwa.utils.cache import get_cached_session_status

ProviderChoice = Literal["meta", "openwa"]
_RESOLVER_CACHE_TTL = 5

_META_MODES = {"", "inherit", "Account-level", "Meta Cloud API"}

# Values of the WhatsApp Templates.custom_session_strategy custom field.
STRATEGY_RANDOM = "Random Session"


def resolve_provider(
	account_name: str,
	requested_provider: str | None = None,
	session_strategy: str | None = None,
) -> tuple[ProviderChoice, str | None]:
	"""Return (provider, session_name). session_name is None when provider == 'meta'.

	session_strategy — the template's OpenWA Session Strategy custom field
	("Default Session" / "Random Session"). None behaves as "Default Session".
	"""
	cache_key = f"openwa:resolver:{account_name}:{requested_provider or ''}:{session_strategy or ''}"
	cached = frappe.cache.get_value(cache_key)
	if cached:
		parts = str(cached).split("|", 1)
		return parts[0], parts[1] if len(parts) > 1 and parts[1] else None  # type: ignore[return-value]

	result = _resolve(account_name, requested_provider, session_strategy)
	frappe.cache.set_value(
		cache_key, f"{result[0]}|{result[1] or ''}", expires_in_sec=_RESOLVER_CACHE_TTL
	)
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


def _resolve_openwa(ext, session_strategy: str | None = None) -> tuple[ProviderChoice, str | None]:
	"""Pick the sending session for the account.

	Sessions are linked to the account via OpenWA Session.linked_whatsapp_account.
	- "Random Session": pick a random healthy session (spreads send volume
	  across the account's sessions); if none are healthy, a random linked one.
	- default: the session flagged is_default; the extension's openwa_session
	  link is kept as a back-compat fallback; finally any linked session.
	"""
	account = ext.linked_whatsapp_account or ext.name
	sessions = _account_sessions(account)

	if sessions:
		if (session_strategy or "").strip() == STRATEGY_RANDOM:
			return "openwa", _pick_random(sessions)
		default = next((s for s in sessions if s["is_default"]), None)
		if default:
			return "openwa", default["name"]

	# Back-compat: the single link on the Provider Extension.
	if ext.openwa_session:
		return "openwa", ext.openwa_session

	if sessions:
		return "openwa", sessions[0]["name"]

	frappe.log_error(
		title="OpenWA Resolver: no session linked",
		message=f"Account {account} has no OpenWA session — falling back to Meta.",
	)
	return "meta", None


def _account_sessions(account: str) -> list[dict]:
	"""All sessions linked to the account, healthy ones first."""
	sessions = frappe.db.get_all(
		"OpenWA Session",
		filters={"linked_whatsapp_account": account},
		fields=["name", "is_default"],
	)
	for s in sessions:
		s["healthy"] = _session_is_healthy(s["name"])
	return sessions


def _pick_random(sessions: list[dict]) -> str:
	healthy = [s["name"] for s in sessions if s["healthy"]]
	pool = healthy or [s["name"] for s in sessions]
	return random.choice(pool)


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
