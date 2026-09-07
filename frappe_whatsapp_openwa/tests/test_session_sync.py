"""Session poll write-gating (prompt3 F4).

The desk form polls get_status every 8 seconds while a session is initialising
or showing a QR. An unconditional doc.save() made each poll a write plus the
full document lifecycle even when nothing had changed.
"""

import unittest
from unittest.mock import MagicMock, patch

_MODULE = "frappe_whatsapp_openwa.api.session"


class _Doc(MagicMock):
	def get(self, field, default=None):
		return getattr(self, field, default)


def _doc(status="QR Required", last_error="", qr="QRDATA", restarts=0, disconnects=0):
	d = _Doc()
	d.name = "OWA-SESS-001"
	d.gateway_session_id = "gw-1"
	d.status = status
	d.last_error = last_error
	d.qr_code_data = qr
	d.restart_attempt_count = restarts
	d.consecutive_disconnect_count = disconnects
	return d


class TestSyncFromGateway(unittest.TestCase):
	def _sync(self, doc, remote_status="qr_ready", last_error=None, qr="QRDATA"):
		frappe_mock = MagicMock()
		frappe_mock.utils.now.return_value = "2026-09-07 12:00:00"
		remote = {"status": remote_status, "lastError": last_error}
		with patch(f"{_MODULE}.frappe", frappe_mock), \
			patch(f"{_MODULE}.get_gateway_client", return_value=MagicMock()), \
			patch(f"{_MODULE}.fetch_session", return_value=remote), \
			patch(f"{_MODULE}.fetch_qr_image", return_value=qr):
			from frappe_whatsapp_openwa.api.session import _sync_from_gateway
			_sync_from_gateway(doc, want_qr=True)
		return doc

	def test_unchanged_poll_does_not_save(self):
		"""The regression: every 8-second poll wrote and fired the full lifecycle."""
		doc = self._sync(_doc())
		doc.save.assert_not_called()

	def test_unchanged_poll_still_records_health_check(self):
		"""Operators must still see that polling is alive."""
		doc = self._sync(_doc())
		doc.db_set.assert_called_once_with(
			"last_health_check", "2026-09-07 12:00:00", update_modified=False
		)

	def test_status_change_saves(self):
		doc = self._sync(_doc(status="Initializing"))
		doc.save.assert_called_once()

	def test_last_error_change_saves(self):
		"""Gating on status alone would stop persisting a new gateway error."""
		doc = self._sync(_doc(last_error=""), last_error="boom")
		doc.save.assert_called_once()

	def test_qr_rotation_saves(self):
		doc = self._sync(_doc(qr="OLDQR"), qr="NEWQR")
		doc.save.assert_called_once()

	def test_counters_reset_on_connect_saves(self):
		"""Already Connected but with stale counters — still needs the reset."""
		doc = self._sync(_doc(status="Connected", qr="", restarts=2), remote_status="ready", qr="")
		doc.save.assert_called_once()

	def test_connected_and_clean_does_not_save(self):
		doc = self._sync(_doc(status="Connected", qr="", restarts=0, disconnects=0),
		                 remote_status="ready", qr="")
		doc.save.assert_not_called()


if __name__ == "__main__":
	unittest.main()
