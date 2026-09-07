"""Phone number utilities.

All callers pass E.164 (+2348012345678).
OpenWA wire format is 2348012345678@c.us (no + prefix, @c.us suffix).
"""

from __future__ import annotations

import re


def to_wa_format(e164: str) -> str:
	"""Convert E.164 → OpenWA @c.us format."""
	digits = re.sub(r"[^\d]", "", e164)
	if not digits:
		raise ValueError(f"Cannot convert '{e164}' to WhatsApp format — no digits found.")
	return f"{digits}@c.us"


def from_wa_format(wa_id: str) -> str:
	"""Strip @c.us / @g.us suffix → bare digit string (not E.164, just digits)."""
	return wa_id.split("@")[0]


def normalise_e164(phone: str) -> str:
	"""Best-effort E.164 normalisation for Nigerian numbers.

	- Already +234... → return as-is
	- 0XXXXXXXXXX    → +234XXXXXXXXX (strip leading 0, prepend +234)
	- 234XXXXXXXXXX  → +234XXXXXXXXXX
	"""
	phone = phone.strip()
	if phone.startswith("+"):
		return phone
	digits = re.sub(r"[^\d]", "", phone)
	if digits.startswith("234") and len(digits) >= 13:
		return f"+{digits}"
	if digits.startswith("0") and len(digits) == 11:
		return f"+234{digits[1:]}"
	return f"+{digits}"


def to_pairing_code_digits(phone: str) -> str:
	"""Normalise a phone number for the gateway pairing-code endpoint.

	The OpenWA ``POST /api/sessions/:id/pairing-code`` contract requires
	digits-only international format (6–15 digits, no ``+``/spaces/dashes).

	Raises ``ValueError`` if the result is outside that range.
	"""
	digits = re.sub(r"[^\d]", "", phone or "")
	if len(digits) < 6 or len(digits) > 15:
		raise ValueError(
			f"Phone number must be 6–15 digits after stripping non-digits; got {len(digits)} ('{phone}')."
		)
	return digits
