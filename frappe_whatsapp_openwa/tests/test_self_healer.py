"""Self-healer restart guard (OWA-05).

The local "Disconnected" status comes from a webhook whose signature covers the
body alone — no timestamp, no nonce — so a captured session.disconnected request
stays valid forever and can be replayed against a healthy session. Restarting on
that unverified signal is what converts a replay into a real outage, so the
healer must confirm the disconnect with the gateway before acting.
"""

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

_MODULE = "frappe_whatsapp_openwa.monitoring.self_healer"
_GATEWAY = "frappe_whatsapp_openwa.utils.gateway"


class _Row(dict):
	"""frappe.get_all returns _dict rows — attribute access, like the real thing."""

	__getattr__ = dict.get


def _session(name="OWA-SESS-001", attempts=0):
	return _Row(
		name=name,
		gateway_session_id="site-sess-001",
		restart_attempt_count=attempts,
		disconnect_grace_until=None,
	)


class TestHealRestartGuard(unittest.TestCase):
	def _run(self, gateway_status):
		"""Run _do_heal against one Disconnected session; gateway_status is what
		the gateway reports (None = gateway unreachable)."""
		frappe_mock = MagicMock()
		settings = MagicMock()
		settings.enable_openwa_provider = 1
		frappe_mock.get_single.return_value = settings
		frappe_mock.get_all.return_value = [_session()]
		frappe_mock.utils.now_datetime.return_value = datetime(2026, 9, 7, 12, 0, 0)

		gw_payload = None if gateway_status is None else {"status": gateway_status}

		with patch(f"{_MODULE}.frappe", frappe_mock), \
			patch(f"{_GATEWAY}.get_gateway_client", return_value=MagicMock()), \
			patch(f"{_GATEWAY}.fetch_session", return_value=gw_payload), \
			patch(f"{_MODULE}._restart_on_gateway", return_value=True) as restart:
			from frappe_whatsapp_openwa.monitoring.self_healer import _do_heal
			_do_heal()
		return restart, frappe_mock

	def test_connected_gateway_suppresses_restart(self):
		"""Gateway reports "ready" — the session is live — the Disconnected flag was stale or forged,
		so restarting it would drop a healthy session."""
		restart, frappe_mock = self._run("ready")
		restart.assert_not_called()

	def test_connected_gateway_corrects_local_status(self):
		restart, frappe_mock = self._run("ready")
		frappe_mock.db.set_value.assert_called_once()
		updates = frappe_mock.db.set_value.call_args[0][2]
		self.assertEqual(updates["status"], "Connected")
		self.assertEqual(updates["consecutive_disconnect_count"], 0)

	def test_genuinely_disconnected_session_is_still_restarted(self):
		"""The guard must not stop real healing."""
		restart, _ = self._run("disconnected")
		restart.assert_called_once()

	def test_unreachable_gateway_falls_through_to_restart(self):
		"""fetch_session returns None on timeout — fail open to the previous behaviour."""
		restart, _ = self._run(None)
		restart.assert_called_once()


if __name__ == "__main__":
	unittest.main()
