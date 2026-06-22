"""Daily counter management and log retention for OpenWA sessions."""

import frappe

_WEBHOOK_LOG_RETENTION_DAYS = 7


def reset_daily_message_counts():
	"""Midnight cron: zero out messages_sent_today on all sessions.

	Sends an email alert to System Managers on failure so a missed reset
	(which would lock all sessions at cap) is immediately visible.
	"""
	try:
		frappe.db.sql("UPDATE `tabOpenWA Session` SET messages_sent_today = 0")
		frappe.db.commit()
	except Exception:
		tb = frappe.get_traceback()
		frappe.log_error(title="OpenWA daily counter reset failed", message=tb)

		# Alert System Managers so they can manually reset if needed.
		try:
			recipients = [
				u.email for u in frappe.get_all(
					"Has Role",
					filters={"role": "System Manager", "parenttype": "User"},
					fields=["parent as email"],
				) if u.email and frappe.db.get_value("User", u.email, "enabled")
			]
			if recipients:
				frappe.sendmail(
					recipients=recipients,
					subject="[ALERT] OpenWA daily message counter reset failed",
					message=(
						"<p>The scheduled midnight reset of <b>messages_sent_today</b> on all "
						"OpenWA Sessions failed. Sessions that have already reached their daily "
						"soft cap will continue to queue or fall back to Meta until the counters "
						"are manually reset.</p>"
						f"<pre>{tb}</pre>"
						"<p>To reset manually, run:<br>"
						"<code>frappe.db.sql(\"UPDATE `tabOpenWA Session` "
						"SET messages_sent_today = 0\")</code></p>"
					),
				)
		except Exception:
			frappe.log_error(
				title="OpenWA: failed to send reset-failure alert",
				message=frappe.get_traceback(),
			)


def purge_old_webhook_logs():
	"""Weekly cron: delete processed OpenWA Webhook Log rows older than retention window.

	Unprocessed rows (processed=0, i.e. error rows) are kept for manual review.
	"""
	cutoff = frappe.utils.add_days(frappe.utils.today(), -_WEBHOOK_LOG_RETENTION_DAYS)
	frappe.db.sql(
		"DELETE FROM `tabOpenWA Webhook Log` WHERE processed = 1 AND received_at < %s",
		cutoff,
	)
	frappe.db.commit()
