"""SSRF guard for URLs that this bench, or the gateway, will fetch.

Two different paths hand a URL to a fetcher:

  inbound  — the webhook body names a media URL that *this bench* downloads and
             saves as a File the caller can read back, so an unguarded fetch is
             a full SSRF with exfiltration (Redis, MariaDB, 169.254.169.254).
  outbound — send_media hands a caller-supplied URL to the OpenWA gateway, which
             fetches it. The gateway typically sits inside the same private
             network, so this turns an authenticated API into a probe for
             internal services.

A blanket "reject every private address" is wrong for both, because the hosts
these paths legitimately talk to are usually private: the OpenWA gateway is
commonly on localhost or a LAN address, and a self-hosted Frappe site serving
/files/ may be too. Rejecting those does not fail safe, it just silently stops
media from working.

So the rule is: a URL is fetchable when it is HTTP(S) and either resolves
entirely to public addresses, or points at a host the deployment has explicitly
configured. Configuration is the allowlist; everything else private is refused.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")


def host_of(url: str | None) -> str:
	"""The lowercased hostname of *url*, or "" if it has none."""
	if not url:
		return ""
	try:
		return (urlparse(url).hostname or "").lower()
	except Exception:
		return ""


def is_safe_fetch_url(url: str, allowed_hosts: Iterable[str] = ()) -> bool:
	"""True if *url* may be fetched.

	allowed_hosts — hostnames the deployment has configured (the gateway, the
	site itself). These are permitted even when they resolve to private
	addresses, because that is the normal way these components are deployed.

	Callers must also disable redirect following: a public URL is otherwise
	free to redirect inward after this check has passed.
	"""
	if not url:
		return False

	try:
		parsed = urlparse(url)
	except Exception:
		return False

	if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
		return False

	host = parsed.hostname.lower()
	if host in {h.lower() for h in allowed_hosts if h}:
		return True

	try:
		infos = socket.getaddrinfo(host, parsed.port or 0, proto=socket.IPPROTO_TCP)
	except Exception:
		return False

	if not infos:
		return False

	for info in infos:
		try:
			ip = ipaddress.ip_address(info[4][0])
		except ValueError:
			return False
		if (
			ip.is_loopback
			or ip.is_private
			or ip.is_link_local
			or ip.is_reserved
			or ip.is_multicast
			or ip.is_unspecified
		):
			return False

	return True


def assert_media_url_is_sendable(media_url: str) -> None:
	"""Reject media URLs that would make the gateway probe the internal network.

	The URL is handed to the OpenWA gateway, which fetches it. The gateway
	normally sits inside the same private network as the bench, so an
	unchecked URL lets a caller reach internal services and read the result
	back through the delivered message.

	This lived on the whitelisted send_media endpoint alone, which left every
	other route to the same adapter unguarded: a WhatsApp Message carrying an
	arbitrary `attach`, and the queue worker draining one. It is enforced in
	routing.router now, which all of them go through.

	A site's own /files/ URLs must keep working, and a self-hosted site is often
	on a private address itself, so the site host is allowed explicitly rather
	than being caught by the private-address rule. Relative URLs are site-local
	by definition and are resolved against the site before checking.
	"""
	import frappe

	if not media_url:
		frappe.throw(frappe._("A media URL is required."), frappe.ValidationError)

	site_url = frappe.utils.get_url()
	url = media_url
	if url.startswith("/"):
		url = site_url.rstrip("/") + url

	if not is_safe_fetch_url(url, allowed_hosts=[host_of(site_url)]):
		frappe.throw(
			frappe._("Media URL {0} is not allowed.").format(media_url),
			frappe.ValidationError,
		)
