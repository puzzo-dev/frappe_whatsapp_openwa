"""Gateway client pooling (prompt3 F8).

Every call used to build a new httpx.Client and never close it: a fresh TCP and
TLS handshake per gateway request, and a leaked connection pool each time.
"""

import unittest
from unittest.mock import MagicMock, patch

_MODULE = "frappe_whatsapp_openwa.utils.gateway"


class TestGatewayClientPool(unittest.TestCase):
    def setUp(self):
        from frappe_whatsapp_openwa.utils import gateway
        gateway._CLIENTS.clear()

    def _frappe(self, base_url="https://gw.example", key="k1", site="site-a"):
        m = MagicMock()
        settings = MagicMock()
        settings.gateway_base_url = base_url
        settings.get_password.return_value = key
        m.get_single.return_value = settings
        m.local.site = site
        return m

    def _get(self, frappe_mock, timeout=10.0):
        with patch(f"{_MODULE}.frappe", frappe_mock):
            from frappe_whatsapp_openwa.utils.gateway import get_gateway_client
            return get_gateway_client(timeout=timeout)

    def test_same_config_reuses_one_client(self):
        m = self._frappe()
        self.assertIs(self._get(m), self._get(m))

    def test_different_timeouts_get_their_own_client(self):
        """httpx fixes the timeout at construction, so callers keep theirs."""
        m = self._frappe()
        self.assertIsNot(self._get(m, timeout=3.0), self._get(m, timeout=35.0))

    def test_sites_never_share_a_client(self):
        """A shared client would hand one tenant's credentials to another."""
        a = self._get(self._frappe(site="site-a"))
        b = self._get(self._frappe(site="site-b"))
        self.assertIsNot(a, b)

    def test_rotated_api_key_builds_a_new_client(self):
        old = self._get(self._frappe(key="k1"))
        new = self._get(self._frappe(key="k2"))
        self.assertIsNot(old, new)

    def test_rotating_config_closes_the_stale_client(self):
        from frappe_whatsapp_openwa.utils import gateway
        old = self._get(self._frappe(key="k1"))
        self._get(self._frappe(key="k2"))
        self.assertTrue(old.is_closed, "stale client must not keep sockets open")
        self.assertEqual(len(gateway._CLIENTS), 1)

    def test_closed_client_is_replaced(self):
        m = self._frappe()
        first = self._get(m)
        first.close()
        self.assertIsNot(self._get(m), first)

    def test_unconfigured_gateway_still_throws(self):
        m = self._frappe(base_url="")
        m.throw.side_effect = RuntimeError
        with self.assertRaises(RuntimeError):
            self._get(m)


if __name__ == "__main__":
    unittest.main()
