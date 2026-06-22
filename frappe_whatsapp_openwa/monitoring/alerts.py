import frappe


def check_session_alerts():
	"""Every 5 minutes: alert admin if any session needs human attention."""
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
		fields=["name", "session_name", "status", "phone_number"],
	)
	if not attention_sessions:
		return

	try:
		settings = frappe.get_single("OpenWA Gateway Settings")
		alert_email = settings.fallback_alert_email
	except Exception:
		return

	if not alert_email:
		return

	lines = [f"- {s.session_name} ({s.phone_number}): {s.status}" for s in attention_sessions]
	body = "The following OpenWA sessions require human attention:\n\n" + "\n".join(lines)

	frappe.sendmail(
		recipients=[alert_email],
		subject=f"[OpenWA] {len(attention_sessions)} session(s) need attention",
		message=body,
		now=True,
	)
