"""Gateway sessions must not outlive the documents that own them.

A session created on the gateway is referenced by exactly one Frappe document.
If that reference is never written, or the document is deleted without telling
the gateway, the session keeps running with nothing pointing at it — it cannot
be reconnected or reused, only accumulated.
"""

import unittest
from unittest.mock import MagicMock, patch

_PROV = "frappe_whatsapp_openwa.api.provision"


class TestReleaseGatewaySession(unittest.TestCase):
    def _release(self, status=204, base_url="https://gw.example", skip=False):
        frappe_mock = MagicMock()
        frappe_mock.flags.get.return_value = skip
        settings = MagicMock()
        settings.gateway_base_url = base_url
        frappe_mock.get_single.return_value = settings
        client = MagicMock()
        client.delete.return_value = MagicMock(status_code=status)
        with patch(f"{_PROV}.frappe", frappe_mock), \
            patch("frappe_whatsapp_openwa.utils.gateway.get_gateway_client", return_value=client):
            from frappe_whatsapp_openwa.api.provision import release_gateway_session
            release_gateway_session("OWA-1", "gw-1")
        return client, frappe_mock

    def test_session_is_deleted_on_the_gateway(self):
        client, _ = self._release()
        client.delete.assert_called_once_with("/api/sessions/gw-1")

    def test_already_gone_is_not_an_error(self):
        _, m = self._release(status=404)
        m.log_error.assert_not_called()

    def test_refusal_is_reported_for_a_human(self):
        """The session is still running there and someone has to remove it."""
        _, m = self._release(status=500)
        m.log_error.assert_called_once()

    def test_unconfigured_gateway_is_silent(self):
        client, m = self._release(base_url="")
        client.delete.assert_not_called()
        m.log_error.assert_not_called()

    def test_skip_flag_short_circuits(self):
        client, _ = self._release(skip=True)
        client.delete.assert_not_called()

    def test_blank_id_does_nothing(self):
        frappe_mock = MagicMock()
        with patch(f"{_PROV}.frappe", frappe_mock):
            from frappe_whatsapp_openwa.api.provision import release_gateway_session
            release_gateway_session("OWA-1", "")
        frappe_mock.get_single.assert_not_called()

    def test_unreachable_gateway_never_blocks_the_delete(self):
        frappe_mock = MagicMock()
        frappe_mock.flags.get.return_value = False
        settings = MagicMock(); settings.gateway_base_url = "https://gw.example"
        frappe_mock.get_single.return_value = settings
        with patch(f"{_PROV}.frappe", frappe_mock), \
            patch("frappe_whatsapp_openwa.utils.gateway.get_gateway_client",
                  side_effect=Exception("unreachable")):
            from frappe_whatsapp_openwa.api.provision import release_gateway_session
            release_gateway_session("OWA-1", "gw-1")  # must not raise
        frappe_mock.log_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
