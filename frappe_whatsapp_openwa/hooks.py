app_name = "frappe_whatsapp_openwa"
app_title = "WhatsApp Dual Gateway"
app_publisher = "IVarse Technologies Limited"
app_description = "Routes WhatsApp messages through Meta Cloud API or self-hosted OpenWA gateway"
app_email = "tech@itechnologies.ng"
app_license = "agpl-3.0"
required_apps = ["frappe_whatsapp"]

# ─── DocType class override (primary interception point) ─────────────────
override_doctype_class = {
	"WhatsApp Message": "frappe_whatsapp_openwa.overrides.whatsapp_message.WhatsAppMessageDualGateway",
	"WhatsApp Notification": "frappe_whatsapp_openwa.overrides.notification.WhatsAppNotificationDualGateway",
}

# ─── Override any direct frappe.call() API usage of frappe_whatsapp utils ─
override_whitelisted_methods = {
	"frappe_whatsapp.utils.send_whatsapp_message":
		"frappe_whatsapp_openwa.overrides.send.send_whatsapp_message",
	"frappe_whatsapp.utils.send_template_message":
		"frappe_whatsapp_openwa.overrides.template.send_template_message",
	"frappe_whatsapp.utils.send_media":
		"frappe_whatsapp_openwa.overrides.media.send_media",
}

# ─── DocType events ──────────────────────────────────────────────────────
doc_events = {
	"WhatsApp Account": {
		"validate": "frappe_whatsapp_openwa.overrides.send.validate_account",
		"after_save": "frappe_whatsapp_openwa.overrides.send.ensure_extension_doc",
	},
}

# ─── Scheduled jobs ──────────────────────────────────────────────────────
scheduler_events = {
	"cron": {
		"* * * * *": [
			"frappe_whatsapp_openwa.monitoring.health.ping_all_sessions",
			"frappe_whatsapp_openwa.monitoring.self_healer.heal_disconnected_sessions",
			"frappe_whatsapp_openwa.queue.worker.process_outbound_queue",
		],
		"*/5 * * * *": [
			"frappe_whatsapp_openwa.monitoring.alerts.check_session_alerts",
		],
		"0 0 * * *": [
			"frappe_whatsapp_openwa.monitoring.counters.reset_daily_message_counts",
		],
		"0 3 * * 0": [
			"frappe_whatsapp_openwa.monitoring.counters.purge_old_webhook_logs",
			"frappe_whatsapp_openwa.monitoring.counters.purge_old_outbound_queue_rows",
			"frappe_whatsapp_openwa.monitoring.counters.purge_old_fallback_logs",
		],
		"0 9 * * *": [
			"frappe_whatsapp_openwa.monitoring.dead_letter.report_recent_dead_letters",
		],
	},
}

# ─── Fixtures ────────────────────────────────────────────────────────────
fixtures = [
	{
		"doctype": "Notification",
		"filters": [["module", "=", "WhatsApp Dual Gateway"]],
	},
	{
		"doctype": "Custom Field",
		"filters": [["module", "=", "WhatsApp Dual Gateway"]],
	},
]

# ─── Boot ────────────────────────────────────────────────────────────────
extend_bootinfo = "frappe_whatsapp_openwa.utils.boot.get_bootinfo"

