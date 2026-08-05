import frappe


@frappe.whitelist()
def send_whatsapp_message(account, to, message, provider=None, session_strategy=None, **kwargs):
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
		session_strategy=session_strategy,
	)

	if not result.success:
		frappe.throw(
			frappe._(f"WhatsApp send failed via {result.provider}: {result.error}"),
			title=frappe._("Message Send Failed"),
		)

	return result.message_id


def validate_account(doc, method=None):
	"""Ensure an OpenWA-routed account has at least one linked session.

	Sessions are linked via OpenWA Session.linked_whatsapp_account (multi-session).
	The extension's openwa_session link is kept as a back-compat fallback but is
	no longer required when sessions exist for the account.
	"""
	try:
		ext = frappe.get_doc("WhatsApp Account Provider Extension", doc.name)
	except frappe.DoesNotExistError:
		return

	if ext.default_provider != "OpenWA":
		return

	if ext.openwa_session:
		return

	sessions = frappe.db.get_all(
		"OpenWA Session",
		filters={"linked_whatsapp_account": doc.name},
		fields=["name"],
		limit=1,
	)
	if not sessions:
		frappe.throw(
			frappe._("Cannot set Default Provider to OpenWA without at least one OpenWA Session linked to this account."),
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


