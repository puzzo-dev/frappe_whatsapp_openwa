frappe.ui.form.on("WhatsApp Account Provider Extension", {
	default_provider(frm) {
		if (frm.doc.default_provider === "OpenWA") {
			frm.set_intro(
				__(
					"⚠️ OpenWA uses an unofficial WhatsApp Web connection. " +
					"WhatsApp may ban the phone number used by this account at any time. " +
					"Use it for internal/non-critical messaging only. " +
					"Mission-critical flows (financial confirmations, OTP delivery, regulated comms) " +
					"should stay on Meta Cloud API."
				),
				"orange"
			);
		} else {
			frm.set_intro("");
		}
	},

	refresh(frm) {
		if (frm.doc.default_provider === "OpenWA") {
			frm.trigger("default_provider");
		}
	},
});
