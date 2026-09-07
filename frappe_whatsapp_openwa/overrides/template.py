import frappe


@frappe.whitelist()
def send_template_message(account, to, template_name, body_param=None, provider=None, **kwargs):
	"""Whitelisted entry point for template sends via the dual-gateway stack.

	Resolves provider, flattens the template for OpenWA, or passes through
	to Meta via the doctype override on WhatsApp Message insert.
	"""
	frappe.has_permission("WhatsApp Message", "create", throw=True)

	# The account is client-supplied: authorise it as an object, not just the
	# doctype-wide create right (OWA-03).
	from frappe_whatsapp_openwa.utils.authz import assert_can_send_from_account

	assert_can_send_from_account(account)

	from frappe_whatsapp_openwa.routing.router import route_send_text
	from frappe_whatsapp_openwa.translators.template_flattener import (
		extract_params_from_body_param,
		flatten_template,
	)
	from frappe_whatsapp_openwa.routing.resolver import resolve_provider

	template_strategy = frappe.db.get_value("WhatsApp Templates", template_name, "custom_session_strategy") or None
	resolved_provider, session_name = resolve_provider(account, provider, template_strategy)

	if resolved_provider == "meta":
		# Let the doctype lifecycle handle Meta template sends.
		doc = frappe.new_doc("WhatsApp Message")
		doc.type = "Outgoing"
		doc.to = to
		doc.whatsapp_account = account
		doc.template = template_name
		doc.message_type = "Template"
		if body_param:
			doc.body_param = body_param
		doc.insert()
		return doc.message_id or doc.name

	# OpenWA: flatten the template to plain text.
	template = frappe.get_doc("WhatsApp Templates", template_name)
	params = extract_params_from_body_param(body_param or "[]")
	from frappe_whatsapp_openwa.overrides.whatsapp_message import _extract_button_labels
	text = flatten_template(
		body=template.template or "",
		parameters=params,
		header=template.header or None,
		footer=template.footer or None,
		buttons=_extract_button_labels(template) or None,
	)

	result = route_send_text(
		account_name=account,
		to=to,
		body=text,
		requested_provider=provider,
		session_strategy=template_strategy,
	)
	if not result.success:
		frappe.throw(
			frappe._(f"WhatsApp template send failed via {result.provider}: {result.error}"),
			title=frappe._("Template Send Failed"),
		)
	return result.message_id
