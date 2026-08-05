"""WhatsApp Notification controller subclass.

Registered via hooks.py:
    override_doctype_class = {
        "WhatsApp Notification": "frappe_whatsapp_openwa.overrides.notification.WhatsAppNotificationDualGateway"
    }

Two responsibilities:

1. Multi-company gate (send_template_message)
   The upstream fires ALL matching notifications for a doctype+event regardless
   of company.  We add a guard: if the notification has custom_company set, it
   only fires when the triggering document's company matches.

2. Routing (notify)
   The upstream notify() calls make_post_request() directly to Meta before
   creating a WhatsApp Message doc as a log entry.  Our override intercepts
   before the HTTP call and routes through route_send_text() instead when the
   resolved provider is OpenWA.  Meta accounts pass straight through to
   super().notify() unchanged.
"""

from __future__ import annotations

import frappe

try:
    from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_notification.whatsapp_notification import (
        WhatsAppNotification as _UpstreamNotification,
    )
except ImportError:
    from frappe.model.document import Document as _UpstreamNotification  # type: ignore[assignment]


class WhatsAppNotificationDualGateway(_UpstreamNotification):

    # ── Multi-company gate ────────────────────────────────────────────────

    def send_template_message(self, doc, phone_no=None, default_template=None, ignore_condition=False):
        """Skip this notification when its company doesn't match the document's company.

        A notification with custom_company='' (blank) fires for ALL companies.
        A notification with custom_company='Purwave Lagos' only fires when the
        triggering document has company='Purwave Lagos'.
        """
        notification_company = self.get("custom_company") or ""
        if notification_company:
            doc_company = (
                doc.get("company") if isinstance(doc, dict)
                else getattr(doc, "company", None)
            ) or ""
            if doc_company and doc_company != notification_company:
                return  # wrong company — skip silently

        account_name = self._resolve_account_name(doc)
        if account_name:
            self._check_meta_rate_limit(account_name)
        super().send_template_message(doc, phone_no, default_template, ignore_condition)

    # ── Routing override (notify) ─────────────────────────────────────────

    def notify(self, data: dict, doc_data=None) -> None:
        """Route the notification through OpenWA or Meta based on account config.

        data     — Meta-format payload dict (built by send_template_message)
        doc_data — reference document as_dict() or None for scheduled sends
        """
        account_name = self._resolve_account_name(doc_data)
        if not account_name:
            super().notify(data, doc_data)
            return

        self._check_meta_rate_limit(account_name)

        try:
            from frappe_whatsapp_openwa.routing.resolver import resolve_provider
            session_strategy = frappe.db.get_value(
                "WhatsApp Templates", self.template, "custom_session_strategy"
            ) or None
            provider, session_name = resolve_provider(account_name, None, session_strategy)
        except Exception:
            frappe.log_error(
                title="DualGateway Notification: resolver error — falling back to Meta",
                message=frappe.get_traceback(),
            )
            self._check_meta_rate_limit(account_name)
            super().notify(data, doc_data)
            return

        if provider == "meta":
            self._check_meta_rate_limit(account_name)
            super().notify(data, doc_data)
            return

        # ── OpenWA path ────────────────────────────────────────────────────
        to = data.get("to", "")
        params = _extract_params(data)

        from frappe_whatsapp_openwa.translators.template_flattener import flatten_template
        from frappe_whatsapp_openwa.overrides.whatsapp_message import _extract_button_labels
        template_doc = frappe.get_doc("WhatsApp Templates", self.template)
        body = flatten_template(
            body=template_doc.template or "",
            parameters=params,
            header=template_doc.header or None,
            footer=template_doc.footer or None,
            buttons=_extract_button_labels(template_doc) or None,
        )

        from frappe_whatsapp_openwa.routing.router import route_send_text
        result = route_send_text(
            account_name=account_name, to=to, body=body,
            session_strategy=session_strategy,
        )

        if result.success:
            self._save_message_log(data, doc_data, account_name, result)
            self._apply_set_property(doc_data)
            if result.provider == "openwa" and session_name:
                from frappe_whatsapp_openwa.overrides.whatsapp_message import _increment_session_counter
                _increment_session_counter(session_name)
            self._save_notification_log(meta_data={"provider": result.provider, "message_id": result.message_id})
            frappe.msgprint("WhatsApp Message Triggered", indicator="green", alert=True)
        else:
            self._save_notification_log(meta_data={"error": result.error})
            frappe.msgprint(
                f"Failed to send WhatsApp via {result.provider}: {result.error}",
                indicator="red",
                alert=True,
            )

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _check_meta_rate_limit(self, account_name: str) -> None:
        """Abort the call if the Meta API per-account rate window is full."""
        from frappe_whatsapp_openwa.utils.rate_limiter import check_rate_limit, raise_rate_limit_error

        allowed, context = check_rate_limit(account_name, action="meta_send")
        if not allowed:
            raise_rate_limit_error(account_name, context)

    def _resolve_account_name(self, doc_data=None) -> str | None:
        """Return the WhatsApp account name to use for routing.

        Priority:
          1. whatsapp_account set on the notification (explicit)
          2. Company-specific default outgoing account (from document's company)
          3. Global default outgoing account
        """
        if self.whatsapp_account:
            return self.whatsapp_account

        # Derive company from the reference document
        company = None
        if doc_data:
            company = (
                doc_data.get("company") if isinstance(doc_data, dict)
                else getattr(doc_data, "company", None)
            )

        if company:
            account = frappe.db.get_value(
                "WhatsApp Account",
                {"custom_company": company, "is_default_outgoing": 1},
                "name",
            )
            if account:
                return account

        try:
            from frappe_whatsapp.utils import get_whatsapp_account
            account = get_whatsapp_account(account_type="outgoing")
            return account.name if account else None
        except Exception:
            return None

    def _save_message_log(self, data: dict, doc_data, account_name: str, result) -> None:
        """Create the WhatsApp Message record (mirrors upstream notify success block)."""
        dt = dn = None
        if doc_data:
            dt = doc_data.get("doctype") if isinstance(doc_data, dict) else getattr(doc_data, "doctype", None)
            dn = doc_data.get("name") if isinstance(doc_data, dict) else getattr(doc_data, "name", None)

        new_doc = {
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "message": str(data.get("template", "")),
            "to": data.get("to", ""),
            "message_type": "Template",
            "message_id": result.message_id or "",
            "content_type": getattr(self, "content_type", None) or "text",
            "use_template": 1,
            "template": self.template,
            "whatsapp_account": account_name,
            "custom_provider_used": result.provider,
        }
        if dt and dn:
            new_doc.update({"reference_doctype": dt, "reference_name": dn})

        frappe.get_doc(new_doc).save(ignore_permissions=True)

    def _apply_set_property(self, doc_data) -> None:
        """Write set_property_after_alert to the reference doc (mirrors upstream)."""
        if not (doc_data and self.set_property_after_alert and self.property_value):
            return
        dt = doc_data.get("doctype") if isinstance(doc_data, dict) else getattr(doc_data, "doctype", None)
        dn = doc_data.get("name") if isinstance(doc_data, dict) else getattr(doc_data, "name", None)
        if not (dt and dn):
            return
        meta = frappe.get_meta(dt)
        df = meta.get_field(self.set_property_after_alert)
        if not df:
            return
        value = self.property_value
        if df.fieldtype in frappe.model.numeric_fieldtypes:
            value = frappe.utils.cint(value)
        frappe.db.set_value(dt, dn, self.set_property_after_alert, value)

    def _save_notification_log(self, meta_data: dict) -> None:
        """Insert a WhatsApp Notification Log record (mirrors upstream finally block)."""
        try:
            frappe.get_doc({
                "doctype": "WhatsApp Notification Log",
                "template": self.template,
                "meta_data": meta_data,
            }).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(
                title="DualGateway: failed to save WhatsApp Notification Log",
                message=frappe.get_traceback(),
            )


def _extract_params(data: dict) -> list[str]:
    """Pull ordered text parameters from the Meta template component payload."""
    for component in (data.get("template") or {}).get("components", []):
        if component.get("type") == "body":
            return [p.get("text", "") for p in component.get("parameters", [])]
    return []
