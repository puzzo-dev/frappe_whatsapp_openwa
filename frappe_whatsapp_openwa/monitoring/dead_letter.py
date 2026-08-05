"""Dead-letter queue monitoring for outbound WhatsApp messages.

When an outbound queue item exhausts all retries, the worker creates a
WhatsApp Fallback Log record. This module logs those dead letters to the
Error Log so they are visible in the desk without sending email spam.
"""

from __future__ import annotations

import frappe


_REPORT_LOOKBACK_HOURS = 24
_MAX_LOGS_PER_RUN = 10


def report_recent_dead_letters():
	"""Log dead letters from the last 24 hours to Error Log.

	Previously sent Frappe Notifications (email alerts) per dead-letter record.
	Now logs to Error Log for desk visibility without email spam.
	"""
	cutoff = frappe.utils.add_to_date(frappe.utils.now(), hours=-_REPORT_LOOKBACK_HOURS)
	rows = frappe.get_all(
		"WhatsApp Fallback Log",
		filters={
			"triggered_at": [">=", cutoff],
			"fallback_succeeded": 0,
		},
		fields=["name", "whatsapp_account", "recipient_phone", "failure_reason", "triggered_at"],
		order_by="triggered_at desc",
		limit_page_length=_MAX_LOGS_PER_RUN,
	)
	if not rows:
		return

	for row in rows:
		frappe.log_error(
			title=f"WhatsApp Dead Letter: {row.recipient_phone} via {row.whatsapp_account}",
			message=f"Log: {row.name}\nAccount: {row.whatsapp_account}\nRecipient: {row.recipient_phone}\nReason: {row.failure_reason}\nTime: {row.triggered_at}",
		)
