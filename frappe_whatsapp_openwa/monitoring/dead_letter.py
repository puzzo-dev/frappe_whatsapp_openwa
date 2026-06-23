"""Dead-letter queue monitoring for outbound WhatsApp messages.

When an outbound queue item exhausts all retries, the worker creates a
WhatsApp Fallback Log record. This module reports those dead letters so
they are not silently lost.
"""

from __future__ import annotations

import frappe


_REPORT_LOOKBACK_HOURS = 24


def report_recent_dead_letters():
	"""Email administrators a summary of dead letters in the last 24 hours."""
	cutoff = frappe.utils.add_to_date(frappe.utils.now(), hours=-_REPORT_LOOKBACK_HOURS)
	rows = frappe.get_all(
		"WhatsApp Fallback Log",
		filters={
			"triggered_at": [">=", cutoff],
			"fallback_succeeded": 0,
		},
		fields=[
			"name",
			"whatsapp_account",
			"recipient_phone",
			"failure_reason",
			"triggered_at",
		],
		order_by="triggered_at desc",
		limit_page_length=100,
	)
	if not rows:
		return

	admins = frappe.get_all(
		"User",
		filters={
			"enabled": 1,
			"name": ("in", frappe.get_all("Has Role", filters={"role": "System Manager"}, pluck="parent")),
		},
		pluck="email",
	)
	if not admins:
		return

	rows_html = "\n".join(
		f"<tr><td>{r.name}</td><td>{r.whatsapp_account}</td><td>{r.recipient_phone}</td>"
		f"<td>{r.failure_reason}</td><td>{r.triggered_at}</td></tr>"
		for r in rows
	)
	message = f"""<p>WhatsApp Dual Gateway dead-letter summary (last {_REPORT_LOOKBACK_HOURS} hours):</p>
<table border="1" cellpadding="4">
<tr><th>Log</th><th>Account</th><th>Recipient</th><th>Reason</th><th>Time</th></tr>
{rows_html}
</table>
<p>Review and retry manually from WhatsApp Outbound Queue / WhatsApp Fallback Log.</p>"""

	try:
		frappe.sendmail(
			recipients=admins,
			subject=f"WhatsApp Dead-Letter Report ({len(rows)} items)",
			message=message,
		)
	except Exception:
		frappe.log_error(
			title="WhatsApp dead-letter report failed",
			message=frappe.get_traceback(),
		)
