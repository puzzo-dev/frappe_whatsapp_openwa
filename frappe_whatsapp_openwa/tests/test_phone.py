"""Unit tests for utils/phone.py — pure functions, no Frappe dependency."""

from __future__ import annotations

import pytest

from frappe_whatsapp_openwa.utils.phone import (
	from_wa_format,
	normalise_e164,
	to_wa_format,
)


class TestToWaFormat:
	def test_e164_stripped_and_suffixed(self):
		assert to_wa_format("+2348012345678") == "2348012345678@c.us"

	def test_already_digits(self):
		assert to_wa_format("2348012345678") == "2348012345678@c.us"

	def test_spaces_and_dashes_stripped(self):
		assert to_wa_format("+234 801 234 5678") == "2348012345678@c.us"

	def test_empty_raises(self):
		with pytest.raises(ValueError, match="no digits found"):
			to_wa_format("")

	def test_non_digit_string_raises(self):
		with pytest.raises(ValueError):
			to_wa_format("abc-def")


class TestFromWaFormat:
	def test_strips_c_us(self):
		assert from_wa_format("2348012345678@c.us") == "2348012345678"

	def test_strips_g_us(self):
		assert from_wa_format("1234567890123@g.us") == "1234567890123"

	def test_no_suffix(self):
		assert from_wa_format("2348012345678") == "2348012345678"


class TestNormaliseE164:
	def test_already_e164(self):
		assert normalise_e164("+2348012345678") == "+2348012345678"

	def test_local_format(self):
		assert normalise_e164("08012345678") == "+2348012345678"

	def test_bare_234(self):
		assert normalise_e164("2348012345678") == "+2348012345678"

	def test_strips_whitespace(self):
		assert normalise_e164("  +2348012345678  ") == "+2348012345678"
