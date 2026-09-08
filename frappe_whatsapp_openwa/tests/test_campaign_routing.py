"""A campaign chooses which sessions carry it, and that choice is honoured.

Three things have to hold for the choice to mean anything:

  * the pool is a restriction, never widened back to the account's sessions;
  * a spreading mode is re-decided per message, not cached and reused, or the
    whole campaign lands on whichever session won the first resolution;
  * the session the sender's cap slot was claimed on is the session the message
    actually goes out on.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from frappe_whatsapp_openwa.routing.resolver import resolve_provider

_RES = "frappe_whatsapp_openwa.routing.resolver"


class _Dict(dict):
	__getattr__ = dict.get


def _ext():
	return _Dict(
		name="Test Account",
		default_provider="OpenWA",
		routing_mode_override=None,
		openwa_session=None,
		allow_message_level_override=0,
		linked_whatsapp_account=None,
	)


def _sessions(names, default_idx=0, sent=None):
	return [
		{
			"name": n,
			"is_default": i == default_idx,
			"healthy": True,
			"messages_sent_today": (sent or {}).get(n, 0),
		}
		for i, n in enumerate(names)
	]


def _resolve(sessions, mode_override=None, session_pool=None, cache=None):
	import frappe as _frappe

	cache_mock = cache or MagicMock()
	if cache is None:
		cache_mock.get_value.return_value = None
	db_mock = MagicMock()
	db_mock.get_value.return_value = _ext()
	db_mock.get_single_value.return_value = ""

	with patch.object(_frappe, "db", db_mock), \
		patch.object(_frappe, "cache", cache_mock), \
		patch(f"{_RES}._account_sessions", return_value=sessions), \
		patch(f"{_RES}.can_send", return_value=True), \
		patch(f"{_RES}._record_unavailable") as recorded:
		provider, session = resolve_provider(
			"Test Account", None, None,
			mode_override=mode_override, session_pool=session_pool,
		)
	return provider, session, recorded, cache_mock


class TestSessionPool:
	def test_only_pool_sessions_are_chosen(self):
		for _ in range(20):
			_, s, _, _ = _resolve(
				_sessions(["s1", "s2", "s3"]),
				mode_override="Message-level",
				session_pool=["s2", "s3"],
			)
			assert s in ("s2", "s3")

	def test_pool_narrows_account_level_to_the_pooled_session(self):
		# s1 is the account default, but the campaign excluded it.
		_, s, _, _ = _resolve(
			_sessions(["s1", "s2"], default_idx=0),
			mode_override="Account-level",
			session_pool=["s2"],
		)
		assert s == "s2"

	def test_pool_that_no_longer_matches_falls_back_rather_than_widening(self):
		"""A session unlinked mid-campaign must not silently re-open the account."""
		p, s, recorded, _ = _resolve(
			_sessions(["s1", "s2"]),
			mode_override="Message-level",
			session_pool=["gone"],
		)
		assert (p, s) == ("meta", None)
		assert recorded.called

	def test_no_pool_still_uses_every_account_session(self):
		p, s, _, _ = _resolve(_sessions(["s1"]), mode_override="Account-level")
		assert (p, s) == ("openwa", "s1")


class TestSpreadModesAreNotCached:
	"""Caching a spreading choice pins the campaign to one number for the TTL."""

	def test_message_level_result_is_not_written_to_the_cache(self):
		cache = MagicMock()
		cache.get_value.return_value = None
		_resolve(_sessions(["s1", "s2"]), mode_override="Message-level", cache=cache)
		assert not cache.set_value.called

	def test_hybrid_result_is_not_written_to_the_cache(self):
		cache = MagicMock()
		cache.get_value.return_value = None
		_resolve(_sessions(["s1", "s2"]), mode_override="Hybrid", cache=cache)
		assert not cache.set_value.called

	def test_account_level_result_is_cached(self):
		cache = MagicMock()
		cache.get_value.return_value = None
		_resolve(_sessions(["s1", "s2"]), mode_override="Account-level", cache=cache)
		assert cache.set_value.called

	def test_random_session_strategy_is_treated_as_spreading(self):
		"""It means the same thing Message-level means, so it must not cache either."""
		import frappe as _frappe

		cache = MagicMock()
		cache.get_value.return_value = None
		db_mock = MagicMock()
		db_mock.get_value.return_value = _ext()
		db_mock.get_single_value.return_value = ""
		with patch.object(_frappe, "db", db_mock), \
			patch.object(_frappe, "cache", cache), \
			patch(f"{_RES}._account_sessions", return_value=_sessions(["s1", "s2"])), \
			patch(f"{_RES}.can_send", return_value=True), \
			patch(f"{_RES}._record_unavailable"):
			resolve_provider("Test Account", None, "Random Session")
		assert not cache.set_value.called


class TestRouterHonoursTheResolvedSession:
	"""The slot is claimed before the send; the send must use that same session."""

	def test_session_override_skips_a_second_resolution(self):
		from frappe_whatsapp_openwa.routing import router

		with patch.object(router, "resolve_provider") as resolve, \
			patch.object(router, "_try_openwa_then_fallback") as attempt:
			router.route_send_text("A", "+2348000000000", "hi", session_override="pinned")

		assert not resolve.called
		assert attempt.call_args.args[1] == "pinned"

	def test_media_override_skips_a_second_resolution(self):
		from frappe_whatsapp_openwa.routing import router

		with patch.object(router, "resolve_provider") as resolve, \
			patch.object(router, "_try_openwa_then_fallback") as attempt:
			router.route_send_media(
				"A", "+2348000000000", "http://x/y.png", None, "image",
				session_override="pinned",
			)

		assert not resolve.called
		assert attempt.call_args.args[1] == "pinned"


class TestCampaignRoutingLookup:
	"""The campaign's choice survives the cache round-trip intact."""

	def _lookup(self, cached=None, mode="Hybrid", pool=("s1", "s2")):
		import frappe as _frappe
		from frappe_whatsapp_openwa.routing import campaign

		cache = MagicMock()
		cache.get_value.return_value = cached
		db = MagicMock()
		db.get_value.return_value = mode
		with patch.object(_frappe, "cache", cache), \
			patch.object(_frappe, "db", db), \
			patch.object(_frappe, "get_all", create=True,
			             return_value=[_Dict(openwa_session=n) for n in pool]):
			result = campaign.campaign_routing("BULK-WA-2026-00001")
		return result, cache

	def test_not_part_of_a_campaign_is_not_a_lookup(self):
		from frappe_whatsapp_openwa.routing.campaign import campaign_routing

		assert campaign_routing(None) == (None, None)

	def test_reads_mode_and_pool(self):
		(mode, pool), _ = self._lookup()
		assert mode == "Hybrid"
		assert pool == ["s1", "s2"]

	def test_cached_value_parses_back_to_the_same_choice(self):
		(mode, pool), _ = self._lookup(cached="Hybrid|s1,s2")
		assert (mode, pool) == ("Hybrid", ["s1", "s2"])

	def test_a_campaign_that_chose_nothing_is_cached_as_a_miss(self):
		"""Otherwise every message of an unconfigured campaign re-reads the doc."""
		(mode, pool), cache = self._lookup(mode=None, pool=())
		assert (mode, pool) == (None, None)
		assert cache.set_value.called
		# And the sentinel must read back as "no choice", not as a mode named "\x00none".
		(mode2, pool2), _ = self._lookup(cached=cache.set_value.call_args.args[1])
		assert (mode2, pool2) == (None, None)


class TestQueuedCampaignKeepsItsPool:
	def test_enqueued_payload_carries_the_campaign(self):
		"""The queue row is not linked to the message, so the link travels in the payload."""
		from frappe_whatsapp_openwa.overrides.whatsapp_message import (
			WhatsAppMessageDualGateway,
		)

		doc = object.__new__(WhatsAppMessageDualGateway)
		doc.message_type = "Text"
		doc.template = None
		doc.message = "hello"
		doc.attach = None
		doc.content_type = "text"
		doc.whatsapp_account = "WA-ACC-A"
		doc.to = "+2348012345678"
		doc.bulk_message_reference = "BULK-WA-2026-00001"

		with patch("frappe_whatsapp_openwa.queue.enqueue.enqueue_message",
		           return_value="Q-1") as enqueue, \
			patch("frappe_whatsapp_openwa.overrides.whatsapp_message.frappe", MagicMock()):
			doc._enqueue_for_later("sess-1")

		assert enqueue.call_args.kwargs["payload"]["campaign"] == "BULK-WA-2026-00001"


class TestUnavailabilityIsReportedOnce:
	"""The condition holds for every message of a campaign at once.

	Reporting per message inserted thousands of documents a minute during an
	outage — burying the incident in its own noise and hammering the database
	at the worst possible time.
	"""

	def _record(self, cache):
		import frappe as _frappe
		from frappe_whatsapp_openwa.routing import resolver

		with patch.object(_frappe, "cache", cache), \
			patch.object(_frappe, "get_doc", create=True) as get_doc, \
			patch.object(_frappe, "log_error", create=True), \
			patch.object(_frappe, "utils", create=True):
			resolver._record_unavailable("Acct", "no session")
		return get_doc

	def test_first_report_is_written(self):
		cache = MagicMock()
		cache.get_value.return_value = None
		assert self._record(cache).called

	def test_second_report_in_the_window_is_suppressed(self):
		cache = MagicMock()
		cache.get_value.return_value = 1
		assert not self._record(cache).called

	def test_a_cache_failure_still_reports(self):
		"""Losing the alert is worse than writing it twice."""
		cache = MagicMock()
		cache.get_value.side_effect = RuntimeError("redis down")
		assert self._record(cache).called
