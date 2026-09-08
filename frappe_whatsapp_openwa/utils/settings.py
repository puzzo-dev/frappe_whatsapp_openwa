"""Single accessor for the tunable limits on OpenWA Gateway Settings.

Every limit the app enforces is a field on that doctype. The numbers used to be
constants in the modules that enforced them, which meant the value an operator
could see was not the value the code used — the worst kind of setting, because
there was nothing to see at all.

The doctype's own `default` carries the shipped value, so a fresh site behaves
exactly as before. `fallback` here is not a second opinion about policy: it
covers the case where the settings record cannot be read at all (an install
mid-migration, a broken Single), where refusing to act would be worse than
proceeding as the app always has.

A stored 0 means "no limit" wherever a limit is optional; where zero would be
nonsense — a batch size, a retention window — it is treated as unset and the
shipped default applies.
"""

import frappe

_DOCTYPE = "OpenWA Gateway Settings"


def limit(fieldname: str, fallback: int, zero_means_unlimited: bool = False) -> int:
	"""Read an integer limit from the gateway settings.

	zero_means_unlimited — when True a stored 0 is returned as 0 and the caller
	treats it as "no ceiling". When False, 0 is taken as unset and `fallback`
	applies, because a batch size or retention window of zero would stall the
	feature rather than unbound it.
	"""
	try:
		value = frappe.db.get_single_value(_DOCTYPE, fieldname)
	except Exception:
		return fallback

	try:
		value = int(value or 0)
	except (TypeError, ValueError):
		return fallback

	if value == 0 and not zero_means_unlimited:
		return fallback
	if value < 0:
		return fallback
	return value


def session_limit(session_name: str, fieldname: str, fallback: int) -> int:
	"""Read a limit that belongs to one WhatsApp number.

	Limits that govern a single number live on the session, because that is
	where they are needed: numbers warm up at different rates, carry different
	traffic, and get restricted independently. A single gateway-wide figure
	would have to be set for the most fragile number and would then throttle
	every other one.

	Install-wide limits — the queue, the purges, the media guard, the Meta API
	window — stay on OpenWA Gateway Settings, where there is one of each to
	configure and no question of which session's value wins.
	"""
	if not session_name:
		return fallback

	try:
		value = frappe.db.get_value("OpenWA Session", session_name, fieldname)
	except Exception:
		return fallback

	try:
		value = int(value or 0)
	except (TypeError, ValueError):
		return fallback

	return value if value > 0 else fallback
