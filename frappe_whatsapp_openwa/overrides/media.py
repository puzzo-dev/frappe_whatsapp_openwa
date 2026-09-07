import frappe


@frappe.whitelist()
def send_media(account, to, media_url, caption=None, media_type="image", provider=None, template=None, **kwargs):
	"""Override for frappe_whatsapp.utils.send_media. Routes through dual-gateway stack."""
	# Mirrors upstream: WhatsApp Message is restricted to System Manager.
	frappe.has_permission("WhatsApp Message", "create", throw=True)

	# The account is client-supplied: authorise it as an object, not just the
	# doctype-wide create right (OWA-03).
	from frappe_whatsapp_openwa.utils.authz import assert_can_send_from_account

	assert_can_send_from_account(account)

	_assert_media_url_is_sendable(media_url)

	from frappe_whatsapp_openwa.routing.router import route_send_media

	session_strategy = None
	if template:
		session_strategy = frappe.db.get_value(
			"WhatsApp Templates", template, "custom_session_strategy"
		) or None

	result = route_send_media(
		account_name=account,
		to=to,
		media_url=media_url,
		caption=caption,
		media_type=media_type,
		requested_provider=provider,
		session_strategy=session_strategy,
	)

	if not result.success:
		frappe.throw(
			frappe._(f"WhatsApp media send failed via {result.provider}: {result.error}"),
			title=frappe._("Media Send Failed"),
		)

	return result.message_id


def _assert_media_url_is_sendable(media_url: str) -> None:
	"""Reject media URLs that would make the gateway probe the internal network.

	The URL is passed to the OpenWA gateway, which fetches it. The gateway
	normally sits inside the same private network as the bench, so without this
	an authenticated caller can use it to reach internal services and read the
	result back through the delivered message (OWA-09).

	A site's own /files/ URLs must keep working, and a self-hosted site is often
	on a private address itself, so the site host is allowed explicitly rather
	than being caught by the private-address rule. Relative URLs are site-local
	by definition and are resolved against the site before checking.
	"""
	from frappe_whatsapp_openwa.utils.urlguard import host_of, is_safe_fetch_url

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
