import frappe


@frappe.whitelist()
def send_media(account, to, media_url, caption=None, media_type="image", provider=None, session_strategy=None, **kwargs):
	"""Override for frappe_whatsapp.utils.send_media. Routes through dual-gateway stack."""
	# Mirrors upstream: WhatsApp Message is restricted to System Manager.
	frappe.has_permission("WhatsApp Message", "create", throw=True)

	from frappe_whatsapp_openwa.routing.router import route_send_media

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
