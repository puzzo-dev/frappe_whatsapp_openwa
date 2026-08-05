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
	ext.linked_whatsapp_account = None
	return ext


def _sessions(names, default_idx=0, healthy=True):
	"""Build a list of session dicts as _account_sessions would return."""
	return [
		{"name": n, "is_default": i == default_idx, "healthy": healthy}
		for i, n in enumerate(names)
	]


class TestResolveProvider:
	def _call(self, ext, requested=None, session_healthy=True, sessions=None, strategy=None):
		import frappe as _frappe

		cache_mock = MagicMock()
		cache_mock.get_value.return_value = None  # always cache miss

		with (
			patch.object(_frappe, "get_doc", return_value=ext),
			patch.object(_frappe, "cache", cache_mock),
			patch(
				"frappe_whatsapp_openwa.routing.resolver._session_is_healthy",
				return_value=session_healthy,
			),
			patch(
				"frappe_whatsapp_openwa.routing.resolver._account_sessions",
				return_value=sessions or [],
			),
		):
			return resolve_provider("Test Account", requested, strategy)

	def test_meta_by_default(self):
		ext = _make_ext(default_provider="Meta Cloud API")
		p, s = self._call(ext)
		assert p == "meta"
		assert s is None

	def test_openwa_direct_backcompat(self):
		"""No linked sessions → fall back to ext.openwa_session link."""
		ext = _make_ext(default_provider="OpenWA")
		p, s = self._call(ext, sessions=[])
		assert p == "openwa"
		assert s == "sess-1"

	def test_openwa_default_session(self):
		"""Picks the is_default session from the linked sessions list."""
		ext = _make_ext(default_provider="OpenWA", openwa_session=None)
		p, s = self._call(ext, sessions=_sessions(["s1", "s2"], default_idx=1))
		assert p == "openwa"
		assert s == "s2"

	def test_openwa_random_strategy(self):
		"""Random strategy picks from healthy sessions."""
		ext = _make_ext(default_provider="OpenWA", openwa_session=None)
		with patch("frappe_whatsapp_openwa.routing.resolver.random.choice", return_value="s2"):
			p, s = self._call(
				ext, sessions=_sessions(["s1", "s2", "s3"], default_idx=0),
				strategy="Random Session",
			)
		assert p == "openwa"
		assert s == "s2"

	def test_openwa_random_strategy_all_unhealthy(self):
		"""Random strategy with no healthy sessions still picks one."""
		ext = _make_ext(default_provider="OpenWA", openwa_session=None)
		with patch("frappe_whatsapp_openwa.routing.resolver.random.choice", return_value="s3"):
			p, s = self._call(
				ext, sessions=_sessions(["s1", "s2", "s3"], healthy=False),
				strategy="Random Session",
			)
		assert p == "openwa"
		assert s == "s3"

	def test_openwa_default_strategy_explicit(self):
		"""Explicit 'Default Session' strategy picks the is_default session."""
		ext = _make_ext(default_provider="OpenWA", openwa_session=None)
		p, s = self._call(
			ext, sessions=_sessions(["s1", "s2"], default_idx=0),
			strategy="Default Session",
		)
		assert p == "openwa"
		assert s == "s1"

	def test_openwa_no_sessions_no_backcompat_falls_to_meta(self):
		ext = _make_ext(default_provider="OpenWA", openwa_session=None)
		p, s = self._call(ext, sessions=[])
		assert p == "meta"
		assert s is None

	def test_hybrid_healthy_picks_openwa(self):
		ext = _make_ext(default_provider="Meta Cloud API", routing_mode_override="Hybrid")
		p, s = self._call(ext, session_healthy=True, sessions=_sessions(["s1"]))
		assert p == "openwa"
		assert s == "s1"

	def test_hybrid_unhealthy_falls_to_meta(self):
		ext = _make_ext(default_provider="Meta Cloud API", routing_mode_override="Hybrid")
		p, s = self._call(ext, session_healthy=False, sessions=_sessions(["s1"]))
		assert p == "meta"
		assert s is None

	def test_message_level_override_openwa(self):
		ext = _make_ext(default_provider="Meta Cloud API", allow_message_level_override=1)
		p, s = self._call(ext, requested="openwa", sessions=_sessions(["s1"]))
		assert p == "openwa"
		assert s == "s1"

	def test_message_level_override_ignored_when_disabled(self):
		ext = _make_ext(default_provider="Meta Cloud API", allow_message_level_override=0)
		p, s = self._call(ext, requested="openwa")
		assert p == "meta"

	def test_routing_mode_override_takes_precedence(self):
		# Hybrid + unhealthy session beats default_provider="OpenWA"
		ext = _make_ext(
			default_provider="OpenWA",
			routing_mode_override="Hybrid",
		)
		p, s = self._call(ext, session_healthy=False, sessions=_sessions(["s1"]))
		assert p == "meta"
