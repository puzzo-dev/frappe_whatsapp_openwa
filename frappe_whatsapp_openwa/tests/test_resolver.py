"""Routing chooses a session, not a provider.

Routing modes select which of an account's OpenWA sessions carries a message.
All three stay on OpenWA. Meta is not one of the choices — it is what happens
when OpenWA cannot carry the message at all, which is an availability failure
and is recorded so a Notification can report it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from frappe_whatsapp_openwa.routing.resolver import resolve_provider

_RES = "frappe_whatsapp_openwa.routing.resolver"


class _Dict(dict):
    __getattr__ = dict.get


def _make_ext(default_provider="OpenWA", routing_mode_override=None,
              openwa_session=None, allow_message_level_override=0):
    return _Dict(
        name="Test Account",
        default_provider=default_provider,
        routing_mode_override=routing_mode_override,
        openwa_session=openwa_session,
        allow_message_level_override=allow_message_level_override,
        linked_whatsapp_account=None,
    )


def _sessions(names, default_idx=0, healthy=True, sent=None):
    return [
        {
            "name": n,
            "is_default": i == default_idx,
            "healthy": healthy,
            "messages_sent_today": (sent or {}).get(n, 0),
        }
        for i, n in enumerate(names)
    ]


def _resolve(ext, sessions=None, can_send=True, requested=None, strategy=None,
             gateway_default="", mode_override=None):
    """Run the resolver with the gateway and database stubbed out."""
    import frappe as _frappe

    cache_mock = MagicMock()
    cache_mock.get_value.return_value = None  # always a cache miss
    db_mock = MagicMock()
    db_mock.get_value.return_value = ext
    db_mock.get_single_value.return_value = gateway_default

    can = can_send if callable(can_send) else (lambda name: can_send)

    with patch.object(_frappe, "db", db_mock), \
        patch.object(_frappe, "cache", cache_mock), \
        patch(f"{_RES}._account_sessions", return_value=sessions or []), \
        patch(f"{_RES}.can_send", side_effect=lambda name, total=None: can(name)), \
        patch("frappe_whatsapp_openwa.utils.session_cap.gateway_sent_today", return_value=0), \
		patch(f"{_RES}._record_unavailable") as recorded:
        provider, session = resolve_provider(
            "Test Account", requested, strategy, mode_override
        )
    return provider, session, recorded


class TestAccountLevel:
    """One session carries everything, so a recipient always sees one number."""

    def test_uses_the_default_session(self):
        p, s, _ = _resolve(_make_ext(routing_mode_override="Account-level"),
                           _sessions(["s1", "s2"], default_idx=1))
        assert (p, s) == ("openwa", "s2")

    def test_stays_on_openwa(self):
        p, _, _ = _resolve(_make_ext(routing_mode_override="Account-level"),
                           _sessions(["s1"]))
        assert p == "openwa"


class TestMessageLevel:
    """Spread, so no single number carries a campaign on its own."""

    def test_picks_the_least_used_session(self):
        p, s, _ = _resolve(
            _make_ext(routing_mode_override="Message-level"),
            _sessions(["s1", "s2", "s3"], sent={"s1": 90, "s2": 10, "s3": 50}),
        )
        assert (p, s) == ("openwa", "s2")

    def test_skips_sessions_with_no_capacity(self):
        p, s, _ = _resolve(
            _make_ext(routing_mode_override="Message-level"),
            _sessions(["s1", "s2"], sent={"s1": 0, "s2": 5}),
            can_send=lambda name: name != "s1",
        )
        assert s == "s2", "a rate-limited or capped session must not be chosen"

    def test_random_session_strategy_still_spreads(self):
        """Templates configured before routing modes existed keep working."""
        p, s, _ = _resolve(
            _make_ext(), _sessions(["s1", "s2"], sent={"s1": 9, "s2": 1}),
            strategy="Random Session",
        )
        assert s == "s2"


class TestHybrid:
    """The default while it can send, spilling only when it cannot."""

    def test_prefers_the_default_session(self):
        p, s, _ = _resolve(_make_ext(routing_mode_override="Hybrid"),
                           _sessions(["s1", "s2"], default_idx=0))
        assert s == "s1"

    def test_spills_when_the_default_is_out_of_capacity(self):
        """A campaign that outruns one number moves rather than stalling."""
        p, s, _ = _resolve(
            _make_ext(routing_mode_override="Hybrid"),
            _sessions(["s1", "s2"], default_idx=0),
            can_send=lambda name: name != "s1",
        )
        assert s == "s2"

    def test_spills_when_the_default_is_unhealthy(self):
        sessions = _sessions(["s1", "s2"], default_idx=0)
        sessions[0]["healthy"] = False
        p, s, _ = _resolve(_make_ext(routing_mode_override="Hybrid"), sessions)
        assert s == "s2"


class TestMetaIsOnlyAFallback:
    def test_no_sessions_falls_back_and_is_recorded(self):
        p, s, recorded = _resolve(_make_ext(), sessions=[])
        assert (p, s) == ("meta", None)
        recorded.assert_called_once()

    def test_nothing_able_to_send_falls_back_and_is_recorded(self):
        p, s, recorded = _resolve(
            _make_ext(routing_mode_override="Message-level"),
            _sessions(["s1", "s2"]), can_send=False,
        )
        assert p == "meta"
        recorded.assert_called_once()

    def test_a_meta_account_is_a_choice_not_a_fallback(self):
        """An account set to Meta is not an incident and is not recorded."""
        p, _, recorded = _resolve(_make_ext(default_provider="Meta Cloud API"),
                                  _sessions(["s1"]))
        assert p == "meta"
        recorded.assert_not_called()

    def test_healthy_session_never_falls_back(self):
        p, _, recorded = _resolve(_make_ext(), _sessions(["s1"]))
        assert p == "openwa"
        recorded.assert_not_called()


class TestMessageLevelCrossProvider:
    """Message-level is the only mode that lets one message change provider."""

    def test_can_send_a_single_message_via_meta(self):
        ext = _make_ext(routing_mode_override="Message-level",
                        allow_message_level_override=1)
        p, _, _ = _resolve(ext, _sessions(["s1"]), requested="meta")
        assert p == "meta"

    def test_needs_the_account_permission(self):
        ext = _make_ext(routing_mode_override="Message-level",
                        allow_message_level_override=0)
        p, _, _ = _resolve(ext, _sessions(["s1"]), requested="meta")
        assert p == "openwa", "without permission the account's routing stands"

    def test_account_level_ignores_the_request(self):
        ext = _make_ext(routing_mode_override="Account-level",
                        allow_message_level_override=1)
        p, _, _ = _resolve(ext, _sessions(["s1"]), requested="meta")
        assert p == "openwa"


class TestModePrecedence:
    def test_caller_override_beats_the_account(self):
        ext = _make_ext(routing_mode_override="Account-level")
        p, s, _ = _resolve(ext, _sessions(["s1", "s2"], sent={"s1": 9, "s2": 1}),
                           mode_override="Message-level")
        assert s == "s2"

    def test_account_beats_the_gateway_default(self):
        ext = _make_ext(routing_mode_override="Account-level")
        p, s, _ = _resolve(ext, _sessions(["s1", "s2"], default_idx=0,
                                          sent={"s1": 9, "s2": 1}),
                           gateway_default="Message-level")
        assert s == "s1"

    def test_gateway_default_applies_when_the_account_has_none(self):
        p, s, _ = _resolve(_make_ext(), _sessions(["s1", "s2"], sent={"s1": 9, "s2": 1}),
                           gateway_default="Message-level")
        assert s == "s2"
