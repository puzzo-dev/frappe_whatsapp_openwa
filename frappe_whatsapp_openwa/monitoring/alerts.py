import frappe


def check_session_alerts():
	"""Every 5 minutes: alert admin if any session needs human attention.

	Acts as a polling safety net — the primary alert fires automatically via the
	'openwa-session-needs-attention' Notification when requires_human_attention
	changes during a save.  This job catches sessions that reached that state
	without triggering the automatic notification (e.g. set via db.set_value).
	"""
	from frappe_whatsapp_openwa.utils.cron import acquire_cron_lock, release_cron_lock

	if not acquire_cron_lock("session_alerts", ttl_seconds=270):
		return

	try:
		_do_check_alerts()
	finally:
		release_cron_lock("session_alerts")


def _do_check_alerts():
	"""Inner logic for check_session_alerts — runs under cron lock."""
	attention_sessions = frappe.get_all(
		"OpenWA Session",
		filters={"requires_human_attention": 1},
		fields=["name"],
	)
	if not attention_sessions:
		return

	try:
		notification = frappe.get_doc("Notification", "openwa-session-needs-attention")
	except frappe.DoesNotExistError:
		frappe.log_error(
			title="openwa-session-needs-attention notification not found",
			message="Ensure fixtures are loaded via bench migrate.",
		)
		return

	for row in attention_sessions:
		try:
			session_doc = frappe.get_doc("OpenWA Session", row.name)
			notification.send(session_doc)
		except Exception:
			frappe.log_error(
				title=f"Failed to send attention alert for session {row.name}",
				message=frappe.get_traceback(),
			)
