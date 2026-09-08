"""The message claim has to be released before the insert and held after it.

The two cases pull in opposite directions and getting either wrong loses or
duplicates a customer's message:

  failure before the insert  nothing exists, and holding the claim loses the
                             message for good — the caller releases the
                             *delivery* claim, so the gateway's retry clears
                             that gate and then finds this one still standing,
                             calls it a duplicate and drops it.

  failure after the insert   the row exists and the caller commits it, so
                             releasing would let the retry insert the same
                             message a second time.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

_MOD = "frappe_whatsapp_openwa.api.webhook"


def _run(fail_at: str):
    """Drive _store_inbound_message with a failure injected at `fail_at`."""
    import frappe as _frappe
    from frappe_whatsapp_openwa.api import webhook

    released: list[str] = []
    normalized = {
        "type": "Incoming", "status": "received", "from": "+2348010000000",
        "to": "+2348020000000", "message": "hi", "content_type": "text",
        "profile_name": "Someone", "attach": None,
    }

    def _resolve(_session_id):
        if fail_at == "resolve":
            return None
        return "WA-ACC-A"

    def _insert(*a, **k):
        if fail_at == "insert":
            raise RuntimeError("db down")
        return MagicMock(name="msg_doc")

    def _after(*a, **k):
        if fail_at == "after":
            raise RuntimeError("enqueue failed")

    with patch.object(_frappe, "db", MagicMock()), \
        patch(f"{_MOD}._resolve_account_for_session", side_effect=_resolve), \
        patch(f"{_MOD}._insert_inbound_message", side_effect=_insert), \
        patch(f"{_MOD}._after_inbound_message", side_effect=_after), \
        patch("frappe_whatsapp_openwa.utils.idempotency.release_event",
              side_effect=lambda kind, key: released.append(kind)):
        try:
            webhook._store_inbound_message(
                {"sessionId": "s1"}, MagicMock(), MagicMock(), normalized, "wamid.ABC"
            )
        except Exception:
            pass
    return released


class TestInboundClaimLifecycle:
    def test_failure_before_the_insert_releases_the_claim(self):
        """Otherwise the gateway's retry is dismissed and the message is lost."""
        assert _run("insert") == ["message"]

    def test_unknown_account_releases_the_claim(self):
        """Nothing was written, so a later delivery must be able to succeed."""
        assert _run("resolve") == ["message"]

    def test_failure_after_the_insert_holds_the_claim(self):
        """The row exists and is committed; releasing would duplicate it."""
        assert _run("after") == []

    def test_success_holds_the_claim(self):
        assert _run("none") == []
