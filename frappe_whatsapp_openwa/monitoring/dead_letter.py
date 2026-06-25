"""Dead-letter queue monitoring for outbound WhatsApp messages.

When an outbound queue item exhausts all retries, the worker creates a
WhatsApp Fallback Log record. This module reports those dead letters so
they are not silently lost.
"""

from __future__ import annotations

import frappe


_REPORT_LOOKBACK_HOURS = 24
_MAX_NOTIFICATIONS_PER_RUN = 10


def report_recent_dead_letters():
	"""Notify administrators of dead letters in the last 24 hours via Frappe Notifications.

	Sends one notification per dead-letter record (capped at _MAX_NOTIFICATIONS_PER_RUN
	to prevent alert floods). The 'openwa-dead-letter-alert' Notification handles
	template rendering and recipient management.
	"""
	cutoff = frappe.utils.add_to_date(frappe.utils.now(), hours=-_REPORT_LOOKBACK_HOURS)
	rows = frappe.get_all(
		"WhatsApp Fallback Log",
		filters={
			"triggered_at": [">=", cutoff],
			"fallback_succeeded": 0,
		},
		fields=["name"],
		order_by="triggered_at desc",
		limit_page_length=_MAX_NOTIFICATIONS_PER_RUN,
	)
	if not rows:
		return

	try:
		notification = frappe.get_doc("Notification", "openwa-dead-letter-alert")
	except frappe.DoesNotExistError:
		frappe.log_error(
			title="openwa-dead-letter-alert notification not found",
			message="Ensure fixtures are loaded via bench migrate.",
		)
		return

	for row in rows:
		try:
			doc = frappe.get_doc("WhatsApp Fallback Log", row.name)
			notification.send(doc)
		except Exception:
			frappe.log_error(
				title=f"Dead-letter alert failed for {row.name}",
				message=frappe.get_traceback(),
			)
