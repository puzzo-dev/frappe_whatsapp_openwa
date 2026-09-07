"""Retry belongs to one layer (prompt3 F13).

The queue worker retries a message six times with backoff. The adapter also
retried each call on a network error, so one blip could become many gateway
calls — and a timeout is not proof of non-delivery, so each of those can put
the same message in front of the customer again.
"""

import unittest
from unittest.mock import MagicMock, patch

import httpx

from frappe_whatsapp_openwa.providers.openwa import OpenWAAdapter

_ROUTER = "frappe_whatsapp_openwa.routing.router"


def _adapter(retries):
    a = OpenWAAdapter("https://gw.example", "key", "sess-1", retry_network_errors=retries)
    a._client = MagicMock()
    a._client.post.side_effect = httpx.ConnectError("boom")
    return a


class TestAdapterRetry(unittest.TestCase):
    def test_synchronous_send_still_retries(self):
        """Nothing else retries a desk or API send, so the adapter must."""
        a = _adapter(True)
        with self.assertRaises(httpx.ConnectError):
            a.send_text("+2348012345678", "hi", "ACC")
        self.assertEqual(a._client.post.call_count, 2)

    def test_queued_send_does_not_retry(self):
        """The worker owns the retry loop — one call per attempt."""
        a = _adapter(False)
        with self.assertRaises(httpx.ConnectError):
            a.send_text("+2348012345678", "hi", "ACC")
        self.assertEqual(a._client.post.call_count, 1)

    def test_default_is_to_retry(self):
        """Callers that say nothing keep the safer synchronous behaviour."""
        a = OpenWAAdapter("https://gw.example", "key", "sess-1")
        self.assertTrue(a.retry_network_errors)


class TestRouterThreadsTheFlag(unittest.TestCase):
    def _built_with(self, **kwargs):
        captured = {}

        def _fake(session_name, adapter_retries=True):
            captured["adapter_retries"] = adapter_retries
            return None  # falls through to the Meta fallback, which we stub

        with patch(f"{_ROUTER}._build_openwa_adapter", side_effect=_fake), \
            patch(f"{_ROUTER}.resolve_provider", return_value=("openwa", "sess-1")):
            from frappe_whatsapp_openwa.routing.router import route_send_text
            route_send_text(
                "ACC", "+2348012345678", "hi",
                meta_fallback_fn=lambda: MagicMock(success=True),
                **kwargs,
            )
        return captured["adapter_retries"]

    def test_worker_disables_adapter_retry(self):
        self.assertFalse(self._built_with(adapter_retries=False))

    def test_default_leaves_adapter_retry_on(self):
        self.assertTrue(self._built_with())


if __name__ == "__main__":
    unittest.main()
