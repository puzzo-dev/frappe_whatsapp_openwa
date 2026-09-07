"""Inbound media re-hosting (prompt3 F3, plus the orphaned-attachment bug).

Two separate problems in one code path:

  * The File was attached to the provider's message id rather than the message's
    document name. WhatsApp Message is hash-named, so the reference pointed at a
    document that never exists. Nothing rejected it, but the attachment never
    appeared on the message and — because permission on a private attachment is
    resolved through its parent document — it was readable by nobody.
  * The download ran inline, so the gateway waited on it before receiving a 200.
"""

import sys
import unittest
from types import ModuleType
from unittest.mock import MagicMock, patch

_MODULE = "frappe_whatsapp_openwa.api.webhook"

# _rehost_media imports save_file from frappe.utils.file_manager at call time.
# The conftest frappe stub has no utils package, so provide the module it reaches for.
if "frappe.utils.file_manager" not in sys.modules:
	_fm = ModuleType("frappe.utils.file_manager")
	_fm.save_file = MagicMock()
	sys.modules["frappe.utils.file_manager"] = _fm


class TestRehostAttachesToDocument(unittest.TestCase):
	def test_file_is_attached_to_the_document_name(self):
		"""The regression: dn was the provider message id, which is not a docname."""
		saved = {}

		def _save_file(**kwargs):
			saved.update(kwargs)
			return MagicMock(file_url="/private/files/x.png")

		frappe_mock = MagicMock()
		settings = MagicMock()
		settings.get_password.return_value = "key"

		resp = MagicMock()
		resp.headers = {"content-length": "10", "content-type": "image/png"}
		resp.iter_bytes.return_value = [b"0123456789"]
		client = MagicMock()
		client.stream.return_value.__enter__.return_value = resp

		with patch(f"{_MODULE}.frappe", frappe_mock), \
			patch(f"{_MODULE}._is_safe_media_url", return_value=True), \
			patch("httpx.Client", return_value=client), \
			patch.object(sys.modules["frappe.utils.file_manager"], "save_file", side_effect=_save_file):
			from frappe_whatsapp_openwa.api.webhook import _rehost_media
			_rehost_media("https://gw/media/1.png", "movuufe98t", settings)

		self.assertEqual(saved["dt"], "WhatsApp Message")
		self.assertEqual(saved["dn"], "movuufe98t")


class TestRehostJob(unittest.TestCase):
	def _run(self, exists=True, attach=""):
		frappe_mock = MagicMock()
		frappe_mock.db.exists.return_value = exists
		frappe_mock.db.get_value.return_value = attach
		with patch(f"{_MODULE}.frappe", frappe_mock), \
			patch(f"{_MODULE}._rehost_media", return_value="/private/files/x.png") as rehost:
			from frappe_whatsapp_openwa.api.webhook import rehost_media_job
			rehost_media_job("MSG-1", "https://gw/media/1.png")
		return rehost, frappe_mock

	def test_downloads_and_records_the_hosted_url(self):
		rehost, m = self._run()
		rehost.assert_called_once()
		m.db.set_value.assert_called_once_with(
			"WhatsApp Message", "MSG-1", "attach", "/private/files/x.png",
			update_modified=False,
		)

	def test_deleted_message_is_skipped(self):
		rehost, _ = self._run(exists=False)
		rehost.assert_not_called()

	def test_already_rehosted_is_not_downloaded_again(self):
		"""Job retries and gateway re-delivery must not re-download the file."""
		rehost, _ = self._run(attach="/private/files/x.png")
		rehost.assert_not_called()

	def test_gateway_url_is_still_rehosted(self):
		rehost, _ = self._run(attach="https://gw/media/1.png")
		rehost.assert_called_once()

	def test_failed_download_leaves_attach_alone(self):
		frappe_mock = MagicMock()
		frappe_mock.db.exists.return_value = True
		frappe_mock.db.get_value.return_value = "https://gw/media/1.png"
		with patch(f"{_MODULE}.frappe", frappe_mock), \
			patch(f"{_MODULE}._rehost_media", return_value=None):
			from frappe_whatsapp_openwa.api.webhook import rehost_media_job
			rehost_media_job("MSG-1", "https://gw/media/1.png")
		frappe_mock.db.set_value.assert_not_called()


if __name__ == "__main__":
	unittest.main()
