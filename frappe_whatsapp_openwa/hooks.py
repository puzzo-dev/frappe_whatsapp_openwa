app_name = "frappe_whatsapp_openwa"
app_title = "WhatsApp Dual Gateway"
app_publisher = "I-Varse Technologies NG"
app_description = "Routes WhatsApp messages through Meta Cloud API or self-hosted OpenWA gateway"
app_email = "dev@itechnologies.ng"
app_license = "agpl-3.0"
required_apps = ["frappe_whatsapp"]

# ─── DocType class override (primary interception point) ─────────────────
override_doctype_class = {
	"WhatsApp Message": "frappe_whatsapp_openwa.overrides.whatsapp_message.WhatsAppMessageDualGateway",
	"WhatsApp Notification": "frappe_whatsapp_openwa.overrides.notification.WhatsAppNotificationDualGateway",
	"WhatsApp Templates": "frappe_whatsapp_openwa.overrides.whatsapp_templates.WhatsAppTemplatesDualGateway",
	"Bulk WhatsApp Message":
		"frappe_whatsapp_openwa.overrides.bulk_whatsapp_message.BulkWhatsAppMessageDualGateway",
}

# ─── Client scripts ──────────────────────────────────────────────────────
doctype_js = {
	"Bulk WhatsApp Message": "public/js/bulk_whatsapp_message.js",
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
		# No "validate" hook here. It used to point at send.validate_account,
		# which loads the Provider Extension and returns silently when it does
		# not exist yet — which is every first save, because ensure_extension_doc
		# only creates it in after_save. The rule it was meant to enforce lives
		# in WhatsAppAccountProviderExtension.validate, where the document being
		# validated is the one that holds the setting.
		"after_save": "frappe_whatsapp_openwa.overrides.send.ensure_extension_doc",
	},
}

# ─── Scheduled jobs ──────────────────────────────────────────────────────
scheduler_events = {
	"cron": {
		"* * * * *": [
			"frappe_whatsapp_openwa.queue.worker.process_outbound_queue",
		],
		"*/5 * * * *": [
			"frappe_whatsapp_openwa.monitoring.health.ping_all_sessions",
			"frappe_whatsapp_openwa.monitoring.self_healer.heal_disconnected_sessions",
		],
		"0 0 * * *": [
			"frappe_whatsapp_openwa.monitoring.counters.reset_daily_message_counts",
		],
		"0 3 * * 0": [
			"frappe_whatsapp_openwa.monitoring.counters.purge_old_webhook_logs",
			"frappe_whatsapp_openwa.monitoring.counters.purge_old_outbound_queue_rows",
			"frappe_whatsapp_openwa.monitoring.counters.purge_old_fallback_logs",
			"frappe_whatsapp_openwa.monitoring.dead_letter.report_recent_dead_letters",
		],
	},
}

# ─── Fixtures ────────────────────────────────────────────────────────────
fixtures = [
	{
		"doctype": "Role",
		"filters": [["name", "in", ["OpenWA Manager"]]],
	},
	{
		"doctype": "Custom Field",
		"filters": [["module", "=", "WhatsApp Dual Gateway"]],
	},
]

# ─── Boot ────────────────────────────────────────────────────────────────
extend_bootinfo = "frappe_whatsapp_openwa.utils.boot.get_bootinfo"

# ─── Uninstall cleanup ─────────────────────────────────────────────────────
# Roles have no `module` link, so Frappe's module-based uninstall never removes
# them. Sweep the app-owned role explicitly to avoid orphan residue.
after_migrate = "frappe_whatsapp_openwa.install.after_migrate"
before_uninstall = "frappe_whatsapp_openwa.install.before_uninstall"

