"""Queue worker dispatch coverage (prompt1 Finding 3).

_enqueue_for_later and _dispatch disagreed about how a queued message is
described, so anything queued because a session was unhealthy either failed
outright (templates) or went out empty (media).
"""

import unittest
from unittest.mock import MagicMock, patch

_WORKER = "frappe_whatsapp_openwa.queue.worker"
_ROUTER = "frappe_whatsapp_openwa.routing.router"


class _Row(dict):
	__getattr__ = dict.get


def _row(message_type, account="WA-ACC-A", recipient="+2348012345678"):
	return _Row(
		name="QUEUE-001",
		account=account,
		recipient=recipient,
		message_type=message_type,
		requested_provider="openwa",
	)


class TestDispatchMedia(unittest.TestCase):
	def _dispatch(self, payload):
		with patch(f"{_ROUTER}.route_send_media") as send_media, \
			patch(f"{_ROUTER}.route_send_text"):
			from frappe_whatsapp_openwa.queue.worker import _dispatch
			_dispatch(_row("image"), payload)
		return send_media.call_args.kwargs

	def test_media_url_read_from_attach(self):
		"""_enqueue_for_later stores the URL under "attach", not "media_url"."""
		kwargs = self._dispatch({"body": "a caption", "attach": "https://x/a.png"})
		self.assertEqual(kwargs["media_url"], "https://x/a.png")

	def test_caption_read_from_body(self):
		kwargs = self._dispatch({"body": "a caption", "attach": "https://x/a.png"})
		self.assertEqual(kwargs["caption"], "a caption")

	def test_explicit_media_url_key_still_honoured(self):
		"""Rows already queued in the other shape must still drain."""
		kwargs = self._dispatch({"media_url": "https://y/b.png", "caption": "cap"})
		self.assertEqual(kwargs["media_url"], "https://y/b.png")
		self.assertEqual(kwargs["caption"], "cap")


class TestDispatchTemplate(unittest.TestCase):
	def test_template_is_dispatched_not_rejected(self):
		"""The regression: "template" hit the else branch and died as unsupported."""
		template = MagicMock()
		template.template = "Hello {{1}}, order {{2}}"
		template.header = None
		template.footer = None
		template.buttons = []

		frappe_mock = MagicMock()
		frappe_mock.get_doc.return_value = template

		with patch(f"{_WORKER}.frappe", frappe_mock), \
			patch(f"{_ROUTER}.route_send_text") as send_text:
			from frappe_whatsapp_openwa.queue.worker import _dispatch
			_dispatch(
				_row("template"),
				{"template": "order_update", "body_param": '{"0": "Alice", "1": "ORD-1"}'},
			)

		send_text.assert_called_once()
		body = send_text.call_args.kwargs["body"]
		self.assertIn("Alice", body)
		self.assertIn("ORD-1", body)

	def test_missing_template_name_returns_error_not_crash(self):
		from frappe_whatsapp_openwa.queue.worker import _dispatch
		result = _dispatch(_row("template"), {"body_param": ""})
		self.assertFalse(result.success)

	def test_unknown_message_type_still_rejected(self):
		from frappe_whatsapp_openwa.queue.worker import _dispatch
		result = _dispatch(_row("carrier-pigeon"), {})
		self.assertFalse(result.success)
		self.assertIn("Unsupported", result.error)


if __name__ == "__main__":
	unittest.main()
