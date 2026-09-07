"""SSRF guard for fetched URLs (OWA-04 inbound, OWA-09 outbound).

Also pins the regression the blanket private-address rule would otherwise
cause: the OpenWA gateway and a self-hosted Frappe site are normally on private
addresses, so refusing every private host silently breaks media instead of
failing safe.
"""

import unittest
from unittest.mock import patch

from frappe_whatsapp_openwa.utils.urlguard import host_of, is_safe_fetch_url


def _resolves_to(ip):
	"""Patch getaddrinfo so the test never depends on real DNS."""
	return patch(
		"frappe_whatsapp_openwa.utils.urlguard.socket.getaddrinfo",
		return_value=[(2, 1, 6, "", (ip, 443))],
	)


class TestIsSafeFetchUrl(unittest.TestCase):
	def test_public_host_allowed(self):
		with _resolves_to("140.82.121.4"):
			self.assertTrue(is_safe_fetch_url("https://example.com/a.png"))

	def test_loopback_rejected(self):
		with _resolves_to("127.0.0.1"):
			self.assertFalse(is_safe_fetch_url("http://localhost:13000/x"))

	def test_cloud_metadata_rejected(self):
		with _resolves_to("169.254.169.254"):
			self.assertFalse(is_safe_fetch_url("http://169.254.169.254/latest/meta-data/"))

	def test_private_range_rejected(self):
		with _resolves_to("10.0.0.5"):
			self.assertFalse(is_safe_fetch_url("http://10.0.0.5/admin"))

	def test_non_http_scheme_rejected(self):
		self.assertFalse(is_safe_fetch_url("file:///etc/passwd"))
		self.assertFalse(is_safe_fetch_url("gopher://x/1"))

	def test_empty_rejected(self):
		self.assertFalse(is_safe_fetch_url(""))

	def test_unresolvable_host_rejected(self):
		with patch(
			"frappe_whatsapp_openwa.utils.urlguard.socket.getaddrinfo",
			side_effect=OSError,
		):
			self.assertFalse(is_safe_fetch_url("https://no-such-host.invalid/a.png"))

	# ── configured hosts ────────────────────────────────────────────────
	def test_configured_private_gateway_is_allowed(self):
		"""The gateway is normally on localhost — blocking it breaks media rehosting."""
		with _resolves_to("127.0.0.1"):
			self.assertTrue(
				is_safe_fetch_url("http://localhost:13000/media/x.jpg", allowed_hosts=["localhost"])
			)

	def test_allowlist_does_not_leak_to_other_private_hosts(self):
		with _resolves_to("10.0.0.5"):
			self.assertFalse(
				is_safe_fetch_url("http://10.0.0.5/admin", allowed_hosts=["localhost"])
			)

	def test_allowlist_is_case_insensitive(self):
		with _resolves_to("127.0.0.1"):
			self.assertTrue(
				is_safe_fetch_url("http://GATEWAY.local/x", allowed_hosts=["gateway.local"])
			)

	def test_allowlist_ignores_blank_entries(self):
		"""An unconfigured gateway_base_url must not allowlist everything."""
		with _resolves_to("127.0.0.1"):
			self.assertFalse(is_safe_fetch_url("http://127.0.0.1/x", allowed_hosts=["", None]))


class TestHostOf(unittest.TestCase):
	def test_extracts_host(self):
		self.assertEqual(host_of("https://Gateway.Example:8080/x"), "gateway.example")

	def test_blank_input(self):
		self.assertEqual(host_of(""), "")
		self.assertEqual(host_of(None), "")


if __name__ == "__main__":
	unittest.main()
