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
from frappe_whatsapp_openwa.utils.session_cap import can_send

ProviderChoice = Literal["meta", "openwa"]
_RESOLVER_CACHE_TTL = 5

_META_MODES = {"", "inherit", "Account-level", "Meta Cloud API"}

# Values of the WhatsApp Templates.custom_session_strategy custom field.
STRATEGY_RANDOM = "Random Session"

# Routing modes that deliberately choose a different session per message.
_SPREAD_MODES = ("Message-level", "Hybrid")


def resolve_provider(
	account_name: str,
	requested_provider: str | None = None,
	session_strategy: str | None = None,
	mode_override: str | None = None,
	session_pool: list[str] | None = None,
) -> tuple[ProviderChoice, str | None]:
	"""Return (provider, session_name). session_name is None when provider == 'meta'.

	session_strategy — the template's OpenWA Session Strategy custom field
	("Default Session" / "Random Session"). None behaves as "Default Session".

	session_pool — restrict the choice to these sessions (a campaign picking the
	numbers it is allowed to send from). None means every session on the account.
	"""
	pool = sorted(session_pool) if session_pool else []
	cache_key = (
		f"openwa:resolver:{account_name}:{requested_provider or ''}:"
		f"{session_strategy or ''}:{mode_override or ''}:{','.join(pool)}"
	)
	# Message-level and Hybrid pick a *different* session each message by
	# design, so caching the choice would pin every message to whichever session
	# won the first resolution for the length of the TTL — the exact opposite of
	# spreading a campaign across numbers, which is what those modes are for.
	# Only the one-session modes are stable enough to cache, and whether this is
	# one of them is only known once the effective mode has been resolved.
	cached = frappe.cache.get_value(cache_key)
	if cached:
		parts = str(cached).split("|", 1)
		return parts[0], parts[1] if len(parts) > 1 and parts[1] else None  # type: ignore[return-value]

	provider, session, effective_mode = _resolve(
		account_name, requested_provider, session_strategy, mode_override, session_pool
	)
	if effective_mode not in _SPREAD_MODES:
		frappe.cache.set_value(
			cache_key, f"{provider}|{session or ''}", expires_in_sec=_RESOLVER_CACHE_TTL
		)
	return provider, session


def _resolve(
	account_name: str,
	requested_provider: str | None,
	session_strategy: str | None = None,
	mode_override: str | None = None,
	session_pool: list[str] | None = None,
) -> tuple[ProviderChoice, str | None, str]:
	"""As resolve_provider, plus the routing mode that actually applied.

	The caller needs that third value to decide whether the result may be
	cached: a mode that spreads across sessions must be re-decided per message.
	"""
	# get_doc would also load the enabled_providers child table, which routing
	# never looks at. Only the scalars below are used, so read exactly those.
	# (The resolved result is already cached for _RESOLVER_CACHE_TTL above, so
	# this runs at most once per account per window — no second cache layer,
	# which would only add another thing to invalidate.)
	ext = frappe.db.get_value(
		"WhatsApp Account Provider Extension",
		account_name,
		[
			"name",
			"default_provider",
			"routing_mode_override",
			"allow_message_level_override",
			"openwa_session",
			"linked_whatsapp_account",
		],
		as_dict=True,
	)
	if not ext:
		return "meta", None, ""

	# Per-account choice first; the gateway-wide default covers accounts that
	# have not made one.
	#
	# That default existed as a field and was read by nothing, so setting it
	# changed no routing at all — which matters more than a dead setting
	# usually would. It is there to spread campaign traffic and lean on Meta
	# when OpenWA is not healthy, precisely because OpenWA is an unofficial
	# endpoint and hammering it with a campaign is how a number gets blocked.
	# A protective control that silently does nothing is worse than none,
	# because it is trusted.
	# Precedence: what the caller pinned for this send (a campaign choosing its
	# own routing), then the account's setting, then the gateway default.
	override = (mode_override or "").strip()
	if not override:
		override = (ext.routing_mode_override or "").strip()
	if not override:
		override = _gateway_default_routing_mode()

	# The account decides whether it sends over OpenWA at all. Routing mode then
	# decides *which session* carries the message — it is a choice between this
	# account's sessions, not between providers.
	#
	# Meta is not one of the options. It is the fallback for when OpenWA cannot
	# send at all, and taking it is an incident: _resolve_openwa records it so a
	# Notification can tell someone the gateway is unavailable.
	default = (ext.default_provider or "Meta Cloud API").strip()
	if default != "OpenWA":
		# Message-level lets a caller ask for OpenWA on an otherwise-Meta
		# account, when the account permits it.
		if (
			override == "Message-level"
			and requested_provider
			and requested_provider.lower() == "openwa"
			and ext.allow_message_level_override
		):
			return _resolve_openwa(ext, override, session_strategy, session_pool) + (override,)
		return "meta", None, override

	# Message-level can also send a single message the other way, to Meta.
	if (
		override == "Message-level"
		and requested_provider
		and requested_provider.lower() != "openwa"
		and ext.allow_message_level_override
	):
		return "meta", None, override

	return _resolve_openwa(ext, override, session_strategy, session_pool) + (
		_effective_mode(override, session_strategy),
	)


def _effective_mode(mode: str, session_strategy: str | None) -> str:
	"""The mode _resolve_openwa will actually apply, for the caching decision.

	Mirrors the one rewrite _resolve_openwa performs: a template asking for
	"Random Session" with no mode set means Message-level.
	"""
	if (session_strategy or "").strip() == STRATEGY_RANDOM and not mode:
		return "Message-level"
	return mode


def _gateway_default_routing_mode() -> str:
	"""The site-wide routing mode, for accounts with no explicit override.

	Read on a resolver cache miss rather than per send, so changing it takes
	effect within _RESOLVER_CACHE_TTL rather than immediately — a few seconds,
	which is the same freshness every other routing decision already has.
	"""
	try:
		return (frappe.db.get_single_value("OpenWA Gateway Settings", "default_routing_mode") or "").strip()
	except Exception:
		# Never let a settings read decide a send cannot happen.
		return ""


def _resolve_openwa(
	ext,
	mode: str = "",
	session_strategy: str | None = None,
	session_pool: list[str] | None = None,
) -> tuple[ProviderChoice, str | None]:
	"""Choose which of the account's sessions carries this message.

	The modes differ only in how they pick, and all of them stay on OpenWA:

	  Account-level  one session — the account's default — carries everything.
	                 Predictable, and the recipient always sees the same number.
	  Message-level  spread across the account's sessions, message by message,
	                 so no single number carries a campaign on its own.
	  Hybrid         the default session while it can send, spilling to another
	                 when it cannot. Normal traffic keeps one number; a campaign
	                 that outruns that number's rate or cap moves rather than
	                 stalling or hammering it.

	"Can send" means healthy, inside its send rate and inside its daily cap —
	checked without consuming anything, because several sessions are considered
	and only the chosen one should pay.

	Meta appears here only when nothing can carry the message. That is an
	availability failure rather than a routing decision, so it is recorded for a
	Notification to pick up.
	"""
	# A template asking for "Random Session" predates routing modes and means the
	# same thing Message-level means now: do not put it all on one number.
	if (session_strategy or "").strip() == STRATEGY_RANDOM and not mode:
		mode = "Message-level"

	account = ext.linked_whatsapp_account or ext.name
	sessions = _account_sessions(account)

	if session_pool:
		# A campaign named the numbers it may send from. Honour that literally:
		# quietly widening the pool back to the account would send from a number
		# the sender deliberately excluded.
		allowed = set(session_pool)
		sessions = [s for s in sessions if s["name"] in allowed]
		if not sessions:
			_record_unavailable(
				account,
				"none of the sessions selected for this campaign is still linked to the account",
			)
			return "meta", None

	if not sessions:
		# Back-compat: the single link on the Provider Extension.
		if ext.openwa_session:
			return "openwa", ext.openwa_session
		_record_unavailable(account, "no OpenWA session is linked to this account")
		return "meta", None

	# Worked out once, not once per candidate: the site-wide daily total is the
	# same number whichever session is being asked about, and each candidate
	# used to recompute it with its own full-table SUM.
	from frappe_whatsapp_openwa.utils.session_cap import gateway_sent_today

	sent_today = gateway_sent_today()
	usable = [s for s in sessions if s["healthy"] and can_send(s["name"], sent_today)]
	default = next((s for s in sessions if s["is_default"]), None)

	if mode == "Message-level":
		chosen = _spread(usable)
	elif mode == "Hybrid":
		# The default while it can send; otherwise anything else that can.
		if default and default["healthy"] and can_send(default["name"], sent_today):
			chosen = default["name"]
		else:
			chosen = _spread([s for s in usable if not s["is_default"]] or usable)
	else:
		# Account-level, and the default for anything unrecognised: one session.
		chosen = (default or usable[0] if usable else default)
		chosen = chosen["name"] if isinstance(chosen, dict) else chosen

	if chosen:
		return "openwa", chosen

	# Every session is unhealthy, rate-limited or at its cap.
	_record_unavailable(
		account,
		"no OpenWA session for this account is healthy and within its send rate and daily cap",
	)
	return "meta", None


def _spread(sessions: list[dict]) -> str | None:
	"""Pick the session carrying the least so far, so load evens out.

	Random would drift under load; least-used converges. Ties break randomly so
	two workers starting together do not both choose the same session.
	"""
	if not sessions:
		return None
	fewest = min(s.get("messages_sent_today") or 0 for s in sessions)
	return random.choice([s["name"] for s in sessions if (s.get("messages_sent_today") or 0) == fewest])


def _record_unavailable(account: str, reason: str) -> None:
	"""Record that OpenWA could not carry a message, so someone can be told.

	Written as a WhatsApp Fallback Log row rather than an email or a message
	box: that is a document, so a Frappe Notification on it delivers the alert
	through whatever channel and recipients the site has configured, and stops
	when that Notification is disabled.

	One row per account per window, not one per message. This fires when *no*
	session can carry a message, which is exactly the condition that holds for
	every message of a campaign at once — so it used to insert a full document
	per message, thousands a minute, burying the incident it was reporting in
	its own noise and hammering the database during an outage. The alert says
	the gateway is unavailable; saying it once is the whole message.
	"""
	if not _claim_unavailable_report(account):
		return

	try:
		entry = frappe.get_doc({
			"doctype": "WhatsApp Fallback Log",
			"whatsapp_account": account,
			"attempted_provider": "openwa",
			"fallback_provider": "meta",
			"failure_reason": "OpenWAUnavailable",
			"failure_detail": reason,
			"triggered_at": frappe.utils.now(),
		})
		# This row is the alert. It must not be lost because the account link
		# no longer resolves — an account renamed or removed mid-incident is
		# exactly when someone needs to be told, and a link technicality
		# silently swallowing the record is how an outage goes unnoticed.
		entry.flags.ignore_links = True
		entry.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title="OpenWA: could not record gateway unavailability",
			message=frappe.get_traceback(),
		)


_UNAVAILABLE_REPORT_TTL = 300


def _claim_unavailable_report(account: str) -> bool:
	"""True the first time this account is reported unavailable in the window.

	Fails open: if the cache cannot answer, the row is written. Losing the alert
	is worse than writing it twice.
	"""
	try:
		key = f"openwa:unavailable_reported:{account}"
		if frappe.cache.get_value(key, expires=True):
			return False
		frappe.cache.set_value(key, 1, expires_in_sec=_UNAVAILABLE_REPORT_TTL)
		return True
	except Exception:
		return True


def _account_sessions(account: str) -> list[dict]:
	"""All sessions linked to the account, with their health.

	`status` is selected here rather than left to _session_is_healthy, which
	falls back to a full frappe.get_doc per session on a cache miss. With
	several sessions on an account — the whole point of the multi-session
	model — that was one document load per session on every resolution, to read
	a single column this query can return for free.

	Health precedence is unchanged: the freshly-polled Redis status wins, and
	the stored status is the fallback.
	"""
	sessions = frappe.db.get_all(
		"OpenWA Session",
		filters={"linked_whatsapp_account": account},
		fields=["name", "is_default", "status", "messages_sent_today"],
	)
	for s in sessions:
		cached = get_cached_session_status(s["name"])
		s["healthy"] = (cached if cached is not None else s.get("status")) == "Connected"
	return sessions


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
