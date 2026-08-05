"""Meta Cloud API adapter.

Routes through the upstream WhatsApp Message controller directly,
bypassing our own override so there's no recursion.
"""

from __future__ import annotations

from typing import Literal

from frappe_whatsapp_openwa.providers.base import SendResult, SessionStatus, WhatsAppProvider


class MetaAdapter(WhatsAppProvider):
	"""Thin wrapper that constructs a WhatsApp Message doc and sends it
	via the upstream controller, completely bypassing our override."""

	def __init__(self, account_name: str):
		self.account = account_name

	def send_text(self, to: str, body: str, account: str) -> SendResult:
		import frappe
		from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
			WhatsAppMessage as _Upstream,
		)

		doc = frappe.new_doc("WhatsApp Message")
		doc.type = "Outgoing"
		doc.to = to
		doc.message = body
		doc.content_type = "text"
		doc.whatsapp_account = account or self.account
		try:
			# send_outgoing is called as an unbound method with doc as self —
			# this is intentional: the upstream method expects self to be a
			# Document instance and operates on it directly.
			_Upstream.send_outgoing(doc)
			doc.insert(ignore_permissions=True)
			return SendResult(
				success=doc.status != "Failed",
				provider="meta",
				message_id=doc.message_id or "",
				raw_response={},
			)
		except Exception as e:
			return SendResult(
				success=False, provider="meta", message_id=None, raw_response={}, error=str(e)
			)

	def send_media(
		self,
		to: str,
		media_url: str,
		caption: str | None,
		media_type: Literal["image", "document", "video", "audio"],
		account: str,
	) -> SendResult:
		import frappe
		from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
			WhatsAppMessage as _Upstream,
		)

		doc = frappe.new_doc("WhatsApp Message")
		doc.type = "Outgoing"
		doc.to = to
		doc.message = caption or ""
		doc.attach = media_url
		doc.content_type = media_type
		doc.whatsapp_account = account or self.account
		try:
			_Upstream.send_outgoing(doc)
			doc.insert(ignore_permissions=True)
			return SendResult(
				success=doc.status != "Failed",
				provider="meta",
				message_id=doc.message_id or "",
				raw_response={},
			)
		except Exception as e:
			return SendResult(
				success=False, provider="meta", message_id=None, raw_response={}, error=str(e)
			)

	def send_template(self, to: str, template_name: str, components: dict, account: str) -> SendResult:
		import frappe
		from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
			WhatsAppMessage as _Upstream,
		)

		doc = frappe.new_doc("WhatsApp Message")
		doc.type = "Outgoing"
		doc.to = to
		doc.template = template_name
		doc.message_type = "Template"
		doc.whatsapp_account = account or self.account
		if components.get("body_param"):
			doc.body_param = components["body_param"]
		try:
			_Upstream.send_outgoing(doc)
			doc.insert(ignore_permissions=True)
			return SendResult(
				success=doc.status != "Failed",
				provider="meta",
				message_id=doc.message_id or "",
				raw_response={},
			)
		except Exception as e:
			return SendResult(
				success=False, provider="meta", message_id=None, raw_response={}, error=str(e)
			)

	def get_session_status(self, session_id: str) -> SessionStatus:
		return SessionStatus(status="Connected", details={"provider": "meta"})
