"""Daily counter management and log retention for OpenWA sessions."""

import frappe

_WEBHOOK_LOG_RETENTION_DAYS = 7
_QUEUE_RETENTION_DAYS = 30
_FALLBACK_LOG_RETENTION_DAYS = 90


def reset_daily_message_counts():
	"""Midnight cron: zero out messages_sent_today on all sessions.

	Logs to Error Log on failure so a missed reset is visible in the desk
	without sending email notifications.
	"""
	try:
		frappe.db.sql("UPDATE `tabOpenWA Session` SET messages_sent_today = 0")
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="OpenWA daily counter reset failed",
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


def purge_old_outbound_queue_rows():
	"""Weekly cron: delete terminal outbound queue rows older than retention window."""
	cutoff = frappe.utils.add_days(frappe.utils.today(), -_QUEUE_RETENTION_DAYS)
	frappe.db.sql(
		"DELETE FROM `tabWhatsApp Outbound Queue` WHERE status IN ('Failed', 'Expired', 'Cancelled') AND modified < %s",
		cutoff,
	)
	frappe.db.commit()


def purge_old_fallback_logs():
	"""Weekly cron: delete old fallback/dead-letter logs."""
	cutoff = frappe.utils.add_days(frappe.utils.today(), -_FALLBACK_LOG_RETENTION_DAYS)
	frappe.db.sql(
		"DELETE FROM `tabWhatsApp Fallback Log` WHERE triggered_at < %s",
		cutoff,
	)
	frappe.db.commit()
