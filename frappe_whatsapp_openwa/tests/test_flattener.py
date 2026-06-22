"""Unit tests for translators/template_flattener.py — pure functions."""

from __future__ import annotations

import json

import pytest

from frappe_whatsapp_openwa.translators.template_flattener import (
	extract_params_from_body_param,
	flatten_template,
)


class TestFlattenTemplate:
	def test_simple_substitution(self):
		result = flatten_template("Hello {{1}}, your order is {{2}}.", ["Alice", "ORD-0001"])
		assert result == "Hello Alice, your order is ORD-0001."

	def test_missing_param_leaves_placeholder(self):
		result = flatten_template("Hello {{1}} and {{2}}.", ["Alice"])
		assert result == "Hello Alice and {{2}}."

	def test_no_params(self):
		result = flatten_template("Static message.", [])
		assert result == "Static message."

	def test_header_prepended(self):
		result = flatten_template("Body text.", [], header="Big Header")
		assert result == "Big Header\n\nBody text."

	def test_footer_appended(self):
		result = flatten_template("Body text.", [], footer="Reply STOP to opt out")
		assert result == "Body text.\n\nReply STOP to opt out"

	def test_header_and_footer(self):
		result = flatten_template("Body.", [], header="H", footer="F")
		assert result == "H\n\nBody.\n\nF"

	def test_buttons_appended_as_list(self):
		result = flatten_template("Choose:", [], buttons=["Yes", "No", "Maybe"])
		assert result == "Choose:\n\n1. Yes\n2. No\n3. Maybe"

	def test_full_template(self):
		result = flatten_template(
			body="Hi {{1}}, your invoice {{2}} is due.",
			parameters=["Bob", "INV-2024-0042"],
			header="Payment Reminder",
			footer="Do not reply",
			buttons=["Pay Now", "Contact Us"],
		)
		expected = (
			"Payment Reminder\n\n"
			"Hi Bob, your invoice INV-2024-0042 is due.\n\n"
			"Do not reply\n\n"
			"1. Pay Now\n2. Contact Us"
		)
		assert result == expected

	def test_empty_header_omitted(self):
		result = flatten_template("Body.", [], header="", footer=None)
		assert result == "Body."

	def test_whitespace_in_header_stripped(self):
		result = flatten_template("Body.", [], header="  Trimmed  ")
		assert result == "Trimmed\n\nBody."

	def test_multiple_same_index_substituted(self):
		result = flatten_template("{{1}} and {{1}} again.", ["X"])
		assert result == "X and X again."

	def test_high_index_not_in_params(self):
		result = flatten_template("{{5}} is missing.", ["a", "b"])
		assert result == "{{5}} is missing."


class TestExtractParamsFromBodyParam:
	def test_ordered_by_key(self):
		body = json.dumps({"1": "second", "0": "first"})
		assert extract_params_from_body_param(body) == ["first", "second"]

	def test_empty_string(self):
		assert extract_params_from_body_param("") == []

	def test_single_param(self):
		body = json.dumps({"0": "only"})
		assert extract_params_from_body_param(body) == ["only"]

	def test_values_coerced_to_str(self):
		body = json.dumps({"0": 42, "1": True})
		result = extract_params_from_body_param(body)
		assert result == ["42", "True"]
