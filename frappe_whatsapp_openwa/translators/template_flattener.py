"""Flatten a WhatsApp template + parameter values into a plain-text string.

OpenWA has no native template concept, so we render the template body
client-side before passing it to send_text.

Pure function — no Frappe imports. Takes primitive types only.
"""

from __future__ import annotations

import re


def flatten_template(
	body: str,
	parameters: list[str],
	header: str | None = None,
	footer: str | None = None,
	buttons: list[str] | None = None,
) -> str:
	"""Substitute {{N}} placeholders and assemble the full message string.

	Args:
		body:       Template body text, e.g. "Hello {{1}}, your order {{2}} is ready."
		parameters: Ordered list of substitution values.
		header:     Optional header text (prepended with newline separator).
		footer:     Optional footer text (appended with newline separator).
		buttons:    Optional list of button labels (appended as numbered list).

	Returns:
		Plain text string ready to be sent via OpenWA send_text.
	"""
	rendered = _substitute(body, parameters)
	parts: list[str] = []

	if header:
		parts.append(header.strip())

	parts.append(rendered.strip())

	if footer:
		parts.append(footer.strip())

	if buttons:
		parts.append(_format_buttons(buttons))

	return "\n\n".join(p for p in parts if p)


def _substitute(text: str, params: list[str]) -> str:
	"""Replace {{1}}, {{2}}, ... with params[0], params[1], ..."""
	def replacer(m: re.Match) -> str:
		idx = int(m.group(1)) - 1
		if 0 <= idx < len(params):
			return str(params[idx])
		return m.group(0)

	return re.sub(r"\{\{(\d+)\}\}", replacer, text)


def _format_buttons(labels: list[str]) -> str:
	return "\n".join(f"{i + 1}. {label}" for i, label in enumerate(labels))


def extract_params_from_body_param(body_param_json: str) -> list[str]:
	"""Parse WhatsApp Message.body_param JSON → ordered param list.

	body_param is stored as {"0": "Alice", "1": "ORD-0001"} (string-keyed dict).
	"""
	import json
	if not body_param_json:
		return []
	data = json.loads(body_param_json)
	return [str(v) for _, v in sorted(data.items(), key=lambda kv: int(kv[0]))]
