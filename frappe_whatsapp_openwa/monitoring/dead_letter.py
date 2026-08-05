"""Dead-letter queue monitoring for outbound WhatsApp messages.

When an outbound queue item exhausts all retries, the worker creates a
WhatsApp Fallback Log record. This module logs those dead letters so
they are not silently lost.

The extension does NOT auto-send Frappe Notifications. Users who want
email alerts for dead letters should create their own Frappe Notification
records linked to the WhatsApp Fallback Log doctype.
"""

from __future__ import annotations

import frappe


_REPORT_LOOKBACK_HOURS = 24
_MAX_LOG_PER_RUN = 10


def report_recent_dead_letters():
	"""Log recent dead letters so they appear in the Error Log.

	No notifications are sent — users set up their own Frappe Notification
	records if they want email alerts for dead letters.
	"""
	cutoff = frappe.utils.add_to_date(frappe.utils.now(), hours=-_REPORT_LOOKBACK_HOURS)
	rows = frappe.get_all(
		"WhatsApp Fallback Log",
		filters={
			"triggered_at": [">=", cutoff],
			"fallback_succeeded": 0,
		},
		fields=["name", "whatsapp_account", "recipient_phone", "failure_reason"],
		order_by="triggered_at desc",
		limit_page_length=_MAX_LOG_PER_RUN,
	)
	for row in rows:
		frappe.log_error(
			title=f"OpenWA dead letter: {row.recipient_phone} via {row.whatsapp_account}",
			message=f"Log: {row.name}\nReason: {row.failure_reason}",
		)
