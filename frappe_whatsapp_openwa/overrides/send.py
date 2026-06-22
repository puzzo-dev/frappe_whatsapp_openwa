import frappe


@frappe.whitelist()
def send_whatsapp_message(account, to, message, provider=None, **kwargs):
	"""Override entry point for frappe_whatsapp.utils.send_whatsapp_message.

	Routes through the dual-gateway stack. Falls back to Meta on any failure
	when auto_fallback is enabled.
	"""
	# Mirrors upstream: WhatsApp Message is restricted to System Manager.
	frappe.has_permission("WhatsApp Message", "create", throw=True)

	from frappe_whatsapp_openwa.routing.router import route_send_text

	result = route_send_text(
		account_name=account,
		to=to,
		body=message,
		requested_provider=provider,
	)

	if not result.success:
		frappe.throw(
			frappe._(f"WhatsApp send failed via {result.provider}: {result.error}"),
			title=frappe._("Message Send Failed"),
		)

	return result.message_id


def validate_account(doc, method=None):
	"""Prevent removing the OpenWA session from an extension that is actively routing."""
	try:
		ext = frappe.get_doc("WhatsApp Account Provider Extension", doc.name)
	except frappe.DoesNotExistError:
		return

	if ext.default_provider == "OpenWA" and not ext.openwa_session:
		frappe.throw(
			frappe._("Cannot set Default Provider to OpenWA without a linked OpenWA Session."),
			title=frappe._("Configuration Error"),
		)


def ensure_extension_doc(doc, method=None):
	"""Auto-create a WhatsApp Account Provider Extension when a new account is saved."""
	if frappe.db.exists("WhatsApp Account Provider Extension", doc.name):
		return
	ext = frappe.new_doc("WhatsApp Account Provider Extension")
	ext.linked_whatsapp_account = doc.name
	ext.default_provider = "Meta Cloud API"
	ext.allow_message_level_override = 0
	ext.queue_on_unhealthy = 0
	ext.insert(ignore_permissions=True)


