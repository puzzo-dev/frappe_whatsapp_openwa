"""Unit tests for routing/resolver.py — Frappe is stubbed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from frappe_whatsapp_openwa.routing.resolver import resolve_provider


def _make_ext(
	default_provider="Meta Cloud API",
	routing_mode_override=None,
	openwa_session="sess-1",
	allow_message_level_override=0,
):
	ext = MagicMock()
	ext.default_provider = default_provider
	ext.routing_mode_override = routing_mode_override
	ext.openwa_session = openwa_session
	ext.allow_message_level_override = allow_message_level_override
	return ext


class TestResolveProvider:
	def _call(self, ext, requested=None, session_healthy=True):
		import frappe as _frappe

		cache_mock = MagicMock()
		cache_mock.get.return_value = None  # always cache miss

		with (
			patch.object(_frappe, "get_doc", return_value=ext),
			patch.object(_frappe, "cache", return_value=cache_mock),
			patch(
				"frappe_whatsapp_openwa.routing.resolver._session_is_healthy",
				return_value=session_healthy,
			),
		):
			return resolve_provider("Test Account", requested)

	def test_meta_by_default(self):
		ext = _make_ext(default_provider="Meta Cloud API")
		p, s = self._call(ext)
		assert p == "meta"
		assert s is None

	def test_openwa_direct(self):
		ext = _make_ext(default_provider="OpenWA")
		p, s = self._call(ext)
		assert p == "openwa"
		assert s == "sess-1"

	def test_hybrid_healthy_picks_openwa(self):
		ext = _make_ext(default_provider="Meta Cloud API", routing_mode_override="Hybrid")
		p, s = self._call(ext, session_healthy=True)
		assert p == "openwa"
		assert s == "sess-1"

	def test_hybrid_unhealthy_falls_to_meta(self):
		ext = _make_ext(default_provider="Meta Cloud API", routing_mode_override="Hybrid")
		p, s = self._call(ext, session_healthy=False)
		assert p == "meta"
		assert s is None

	def test_message_level_override_openwa(self):
		ext = _make_ext(default_provider="Meta Cloud API", allow_message_level_override=1)
		p, s = self._call(ext, requested="openwa")
		assert p == "openwa"
		assert s == "sess-1"

	def test_message_level_override_ignored_when_disabled(self):
		ext = _make_ext(default_provider="Meta Cloud API", allow_message_level_override=0)
		p, s = self._call(ext, requested="openwa")
		assert p == "meta"

	def test_openwa_without_session_falls_to_meta(self):
		ext = _make_ext(default_provider="OpenWA", openwa_session=None)
		p, s = self._call(ext)
		assert p == "meta"
		assert s is None

	def test_routing_mode_override_takes_precedence(self):
		# Hybrid + unhealthy session beats default_provider="OpenWA"
		ext = _make_ext(
			default_provider="OpenWA",
			routing_mode_override="Hybrid",
		)
		p, s = self._call(ext, session_healthy=False)
		assert p == "meta"
