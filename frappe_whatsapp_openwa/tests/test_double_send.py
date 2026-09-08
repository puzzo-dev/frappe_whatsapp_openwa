"""Meta double-send guard (prompt1 Findings 1 and 2).

MetaAdapter dispatches through the upstream controller and *then* calls
insert(), so before_insert re-enters send_outgoing with a message_id already
set. The guard must stop that second dispatch for every message type, not just
templates — otherwise every text and media send routed to Meta went out twice.
"""

import unittest
from unittest.mock import MagicMock, patch

_MODULE = "frappe_whatsapp_openwa.overrides.whatsapp_message"


def _doc(message_id="", message_type="Text"):
	from frappe_whatsapp_openwa.overrides.whatsapp_message import WhatsAppMessageDualGateway

	d = object.__new__(WhatsAppMessageDualGateway)
	d.type = "Outgoing"
	d.message_id = message_id
	d.message_type = message_type
	d.template = None
	d.whatsapp_account = "WA-ACC-A"
	d.to = "+2348012345678"
	d.message = "hello"
	d._dual_gateway_base_verified = True  # skip the upstream-import check
	# Sends now authorise the account they send as; these tests are about the
	# dispatch guard, so they run as the internal paths do.
	d.flags = type("_F", (), {"ignore_permissions": True, "custom_ref_doc": None})()
	return d


class TestSendOutgoingGuard(unittest.TestCase):
	def _dispatched(self, doc, provider="openwa"):
		"""True if send_outgoing reached any dispatch path."""
		cls = type(doc)
		with patch(f"{_MODULE}.frappe", MagicMock()), \
			patch(f"{_MODULE}.reserve_slot", return_value=True), \
			patch("frappe_whatsapp_openwa.routing.resolver.resolve_provider",
			      return_value=(provider, "sess-1")), \
			patch.object(cls, "_check_meta_rate_limit"), \
			patch.object(cls, "_should_queue", return_value=False), \
			patch.object(cls, "_send_text_via_openwa") as via_openwa, \
			patch.object(cls, "_send_template_via_openwa") as via_template, \
			patch.object(cls, "_meta_send_result"), \
			patch.object(cls.__mro__[1], "send_outgoing", lambda self: None, create=True):
			doc.send_outgoing()
		return via_openwa.called or via_template.called

	def test_already_sent_text_is_not_resent(self):
		"""The regression: a text with a message_id was dispatched a second time."""
		self.assertFalse(self._dispatched(_doc(message_id="wamid.ABC", message_type="Text")))

	def test_already_sent_template_is_not_resent(self):
		self.assertFalse(self._dispatched(_doc(message_id="wamid.ABC", message_type="Template")))

	def test_already_sent_media_is_not_resent(self):
		self.assertFalse(self._dispatched(_doc(message_id="wamid.ABC", message_type="Media")))

	def test_unsent_message_is_still_dispatched(self):
		"""The guard must not block a genuine first send."""
		self.assertTrue(self._dispatched(_doc(message_id="")))

	def test_failed_retry_is_still_dispatched(self):
		"""A failed send never received a message_id, so retries still work."""
		self.assertTrue(self._dispatched(_doc(message_id=None)))

	def test_incoming_message_never_dispatched(self):
		doc = _doc(message_id="")
		doc.type = "Incoming"
		self.assertFalse(self._dispatched(doc))


if __name__ == "__main__":
	unittest.main()
