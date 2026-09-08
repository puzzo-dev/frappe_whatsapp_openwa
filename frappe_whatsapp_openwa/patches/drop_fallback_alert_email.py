"""Remove the orphaned Fallback Alert Email setting.

The field promised an email whenever a send fell back from OpenWA to Meta, and
nothing ever read it — no code sent that email. A control that looks like it
does something and does not is worse than no control, because an operator sets
it and then trusts it.

The capability itself was never missing. Every fallback writes a WhatsApp
Fallback Log record, so a Notification on that doctype delivers the alert
through the recipients, conditions and channels the site already manages, and
stops when the Notification is disabled. The settings form now says so.

Removing a field from a Single leaves its row behind in `tabSingles`, still
holding whatever address was typed, so it is deleted here rather than left as
dead data.
"""

import frappe

_DOCTYPE = "OpenWA Gateway Settings"
_FIELD = "fallback_alert_email"


def execute():
	if not frappe.db.table_exists("Singles"):
		return
	frappe.db.delete("Singles", {"doctype": _DOCTYPE, "field": _FIELD})
	frappe.clear_cache(doctype=_DOCTYPE)
