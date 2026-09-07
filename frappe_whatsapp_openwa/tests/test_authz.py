"""Object-level authorisation on the send endpoints (OWA-03).

The send endpoints take the sending account as a client-supplied argument. The
pre-existing `has_permission("WhatsApp Message", "create")` check is
doctype-wide and says nothing about *which* account may be used, so on a
multi-company site a company-scoped sender could bill another company's account.
"""

import unittest
from unittest.mock import MagicMock, patch

_MODULE = "frappe_whatsapp_openwa.utils.authz"


class TestAssertCanSendFromAccount(unittest.TestCase):
	def _run(self, account="WA-ACC-B", exists=True, permitted=True):
		frappe_mock = MagicMock()
		frappe_mock._ = lambda s: s
		frappe_mock.db.exists.return_value = exists
		frappe_mock.has_permission.return_value = permitted
		frappe_mock.PermissionError = PermissionError
		frappe_mock.ValidationError = ValueError
		frappe_mock.throw = MagicMock(side_effect=ValueError)

		with patch(f"{_MODULE}.frappe", frappe_mock):
			from frappe_whatsapp_openwa.utils.authz import assert_can_send_from_account
			assert_can_send_from_account(account)
		return frappe_mock

	def test_permitted_account_passes(self):
		m = self._run(permitted=True)
		m.has_permission.assert_called_once_with("WhatsApp Account", "read", doc="WA-ACC-B")

	def test_unpermitted_account_is_rejected(self):
		"""Cross-company send: the caller may create messages but may not read this account."""
		with self.assertRaises(PermissionError):
			self._run(permitted=False)

	def test_unknown_account_is_rejected(self):
		with self.assertRaises(PermissionError):
			self._run(exists=False)

	def test_unknown_account_message_matches_denial(self):
		"""A distinct 'does not exist' error would let a caller enumerate account names."""
		msgs = []
		for kwargs in ({"exists": False}, {"permitted": False}):
			try:
				self._run(**kwargs)
			except PermissionError as e:
				msgs.append(str(e))
		self.assertEqual(msgs[0], msgs[1])

	def test_blank_account_is_rejected(self):
		with self.assertRaises(ValueError):
			self._run(account="")


class TestSendEndpointsCallTheGate(unittest.TestCase):
	"""Each whitelisted send entry point must authorise the account, not just the doctype."""

	def _assert_gated(self, module_path, func_name, *args, **kwargs):
		frappe_mock = MagicMock()
		frappe_mock._ = lambda s: s
		with patch(f"{module_path}.frappe", frappe_mock), \
			patch(f"{_MODULE}.assert_can_send_from_account", side_effect=PermissionError) as gate:
			mod = __import__(module_path, fromlist=[func_name])
			with self.assertRaises(PermissionError):
				getattr(mod, func_name)(*args, **kwargs)
		gate.assert_called_once()

	def test_send_whatsapp_message_is_gated(self):
		self._assert_gated(
			"frappe_whatsapp_openwa.overrides.send",
			"send_whatsapp_message",
			"WA-ACC-B", "+2348012345678", "hi",
		)

	def test_send_template_message_is_gated(self):
		self._assert_gated(
			"frappe_whatsapp_openwa.overrides.template",
			"send_template_message",
			"WA-ACC-B", "+2348012345678", "tmpl",
		)

	def test_send_media_is_gated(self):
		self._assert_gated(
			"frappe_whatsapp_openwa.overrides.media",
			"send_media",
			"WA-ACC-B", "+2348012345678", "https://example.com/a.png",
		)


if __name__ == "__main__":
	unittest.main()
