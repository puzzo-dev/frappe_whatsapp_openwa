"""Delivery-ack ordering (prompt1 Finding 10).

Acks can arrive out of order, and an old one can be replayed verbatim because
the webhook signature covers the body alone. Applying them blindly walked a
message's status backwards.
"""

import unittest
from unittest.mock import MagicMock, patch

_MODULE = "frappe_whatsapp_openwa.api.webhook"


class TestAckSupersedes(unittest.TestCase):
	def _call(self, current, incoming):
		from frappe_whatsapp_openwa.api.webhook import _ack_supersedes
		return _ack_supersedes(current, incoming)

	def test_forward_move_applies(self):
		self.assertTrue(self._call("Sent", "Delivered"))
		self.assertTrue(self._call("Delivered", "Read"))
		self.assertTrue(self._call("Pending", "Read"))

	def test_backward_move_rejected(self):
		"""The regression: a stale or replayed "sent" after "read"."""
		self.assertFalse(self._call("Read", "Sent"))
		self.assertFalse(self._call("Delivered", "Pending"))

	def test_same_state_is_not_reapplied(self):
		self.assertFalse(self._call("Read", "Read"))

	def test_failed_always_applies(self):
		"""Failed is not part of the delivery progression."""
		self.assertTrue(self._call("Read", "Failed"))
		self.assertTrue(self._call("Failed", "Sent"))

	def test_unknown_current_state_applies(self):
		self.assertTrue(self._call(None, "Sent"))
		self.assertTrue(self._call("", "Delivered"))


class TestHandleAckUsesOrdering(unittest.TestCase):
	def _run(self, current, incoming):
		frappe_mock = MagicMock()
		frappe_mock.db.get_value.return_value = {"name": "MSG-1", "status": current}
		log = MagicMock()
		with patch(f"{_MODULE}.frappe", frappe_mock), \
			patch(f"{_MODULE}.normalize_ack_event", create=True), \
			patch("frappe_whatsapp_openwa.translators.webhook_normalizer.normalize_ack_event",
			      return_value={"message_id": "wamid.A", "ack_status": incoming}):
			from frappe_whatsapp_openwa.api.webhook import _handle_ack
			_handle_ack({}, log)
		return frappe_mock

	def test_forward_ack_written(self):
		m = self._run("Sent", "Read")
		m.db.set_value.assert_called_once_with("WhatsApp Message", "MSG-1", "status", "Read")

	def test_backward_ack_not_written(self):
		m = self._run("Read", "Sent")
		m.db.set_value.assert_not_called()


if __name__ == "__main__":
	unittest.main()
