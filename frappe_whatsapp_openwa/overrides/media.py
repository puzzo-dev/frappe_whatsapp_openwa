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
	"""Kept as the endpoint's own name for it; the rule lives in utils.urlguard
	so every send path enforces the same one."""
	from frappe_whatsapp_openwa.utils.urlguard import assert_media_url_is_sendable

	assert_media_url_is_sendable(media_url)
