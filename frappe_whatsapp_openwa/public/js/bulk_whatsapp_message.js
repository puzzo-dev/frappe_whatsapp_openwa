// Campaign-level OpenWA routing.
//
// The session picker is scoped to the campaign's own WhatsApp Account: a
// session linked to another account cannot carry this campaign, and the server
// rejects it on save. Filtering here means the sender never picks one.

frappe.ui.form.on("Bulk WhatsApp Message", {
	setup(frm) {
		frm.set_query("openwa_session", "custom_openwa_sessions", () => ({
			filters: {
				linked_whatsapp_account: frm.doc.whatsapp_account || "",
			},
		}));
	},

	whatsapp_account(frm) {
		// Sessions chosen for the previous account cannot send from this one,
		// so drop them here rather than let the save fail on them.
		const rows = frm.doc.custom_openwa_sessions || [];
		if (!rows.length) {
			return;
		}
		frm.clear_table("custom_openwa_sessions");
		frm.refresh_field("custom_openwa_sessions");
		frappe.show_alert({
			message: __("Sending sessions cleared — they belonged to the previous account."),
			indicator: "orange",
		});
	},
});
