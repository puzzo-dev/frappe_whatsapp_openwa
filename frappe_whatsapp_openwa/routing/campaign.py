"""Per-campaign routing: the sessions a Bulk WhatsApp Message may send from.

A campaign is the case the routing modes exist for. Normal traffic can sit on
one number quite happily; a few thousand campaign messages down a single
unofficial WhatsApp endpoint is how that number gets blocked. So the campaign
itself — not the account, and not the template — gets to say which numbers
carry it and how they are picked.

The choice is read at send time rather than stamped onto each queued message,
because "which session can take this one" depends on live send rate and daily
cap. A session picked at queue time would be a guess made minutes early.
"""

from __future__ import annotations

import frappe

# The campaign's routing fields are not allow_on_submit, and messages only send
# after submit, so the values cannot change while a campaign is running. The
# cache is a short one anyway: a stale read here would outlive an amend.
_CACHE_TTL = 300
_MISS = "\x00none"


def campaign_routing(bulk_name: str | None) -> tuple[str | None, list[str] | None]:
	"""Return (routing_mode, session_pool) for a campaign, or (None, None).

	(None, None) is the answer for a message that is not part of a campaign, for
	a campaign that made no choice, and for a campaign that no longer exists —
	all of which mean "route this the way the account says".
	"""
	if not bulk_name:
		return None, None

	key = f"openwa:campaign_routing:{bulk_name}"
	cached = frappe.cache.get_value(key, expires=True)
	if cached is not None:
		if cached == _MISS:
			return None, None
		mode, _, pool = str(cached).partition("|")
		return (mode or None), ([s for s in pool.split(",") if s] or None)

	mode, pool = _read(bulk_name)
	frappe.cache.set_value(
		key,
		_MISS if (mode is None and pool is None) else f"{mode or ''}|{','.join(pool or [])}",
		expires_in_sec=_CACHE_TTL,
	)
	return mode, pool


def _read(bulk_name: str) -> tuple[str | None, list[str] | None]:
	try:
		mode = frappe.db.get_value("Bulk WhatsApp Message", bulk_name, "custom_routing_mode")
	except Exception:
		# The custom field is missing (fixtures not yet synced). Routing then
		# behaves exactly as it did before campaigns could choose.
		return None, None

	try:
		pool = [
			row.openwa_session
			for row in frappe.get_all(
				"OpenWA Campaign Session",
				filters={"parent": bulk_name, "parenttype": "Bulk WhatsApp Message"},
				fields=["openwa_session"],
				order_by="idx asc",
			)
			if row.openwa_session
		]
	except Exception:
		pool = []

	return ((mode or "").strip() or None), (pool or None)


def clear_campaign_routing(bulk_name: str) -> None:
	"""Drop the cached choice — called when the campaign is saved."""
	frappe.cache.delete_value(f"openwa:campaign_routing:{bulk_name}")
