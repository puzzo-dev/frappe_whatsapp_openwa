frappe.ui.form.on("OpenWA Session", {
	refresh(frm) {
		frm._qr_poll = frm._qr_poll || null;
		_render_qr(frm);
		_add_buttons(frm);
		_subscribe_realtime(frm);
	},

	status(frm) {
		_render_qr(frm);
		_add_buttons(frm);
		if (frm.doc.status === "Failed") {
			frappe.show_alert({
				message: __("This session needs attention. Check the Last Error field in the Diagnostics section."),
				indicator: "red",
			}, 10);
		}
	},

	before_unload(frm) {
		_stop_qr_poll(frm);
		_unsubscribe_realtime(frm);
	},
});


function _render_qr(frm) {
	const wrapper = frm.fields_dict.qr_code_data && frm.fields_dict.qr_code_data.wrapper;

	if (frm.doc.status === "QR Required" && frm.doc.qr_code_data) {
		frm.set_intro(
			__("Scan the QR code with the WhatsApp phone: WhatsApp → Linked Devices → Link a Device. The code refreshes automatically."),
			"orange"
		);
		if (wrapper) {
			const raw = frm.doc.qr_code_data;
			const src = raw.startsWith("data:") ? raw : `data:image/png;base64,${raw}`;
			$(wrapper).empty().append(`
				<div style="text-align:center;padding:20px">
					<img src="${src}"
					     style="max-width:260px;border:2px solid #ff9800;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,0.12)"/>
				</div>`);
		}
		_start_qr_poll(frm);
	} else {
		if (wrapper) $(wrapper).empty();
		if (frm.doc.status === "QR Required") {
			frm.set_intro(__("Waiting for QR code from gateway…"), "blue");
			_start_qr_poll(frm);
		} else if (!frm.is_new() && frm.doc.status === "Initializing") {
			const msg = frm.doc.gateway_session_id
				? __("Waiting for the gateway engine to start… the QR code will appear here shortly.")
				: __("Provisioning this session on the gateway… the QR code will appear here shortly.");
			frm.set_intro(msg, "blue");
			_start_qr_poll(frm);
		} else {
			_stop_qr_poll(frm);
			frm.set_intro("");
		}
	}
}


function _add_buttons(frm) {
	frm.clear_custom_buttons();
	if (frm.is_new()) return;

	frm.add_custom_button(__("Refresh Status"), () => {
		frappe.call({
			method: "frappe_whatsapp_openwa.api.session.get_status",
			args: { session_name: frm.doc.name },
			callback() { frm.reload_doc(); },
		});
	});

	if (!frm.doc.gateway_session_id) {
		frm.add_custom_button(__("Create Session on Gateway"), () => {
			frappe.call({
				method: "frappe_whatsapp_openwa.api.provision.provision_session",
				args: { session_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Creating session on gateway…"),
				callback() { frm.reload_doc(); },
			});
		}, __("Actions"));
	}

	if (frm.doc.gateway_session_id) {
		frm.add_custom_button(__("Remove from Gateway"), () => {
			frappe.confirm(
				__("Permanently remove this session from the gateway? The WhatsApp link will be broken."),
				() => {
					frappe.call({
						method: "frappe_whatsapp_openwa.api.provision.deprovision_session",
						args: { session_name: frm.doc.name },
						callback() { frm.reload_doc(); },
					});
				}
			);
		}, __("Actions"));
	}

	if (["Disconnected", "Failed"].includes(frm.doc.status)) {
		frm.add_custom_button(__("Restart Session"), () => {
			frappe.confirm(
				__("Issue a restart command to the OpenWA gateway for this session?"),
				() => frm.call("restart_session").then(() => frm.reload_doc())
			);
		}, __("Actions"));
	}

	const status_color = {
		Connected: "green",
		Disconnected: "orange",
		"QR Required": "orange",
		Initializing: "blue",
		Failed: "red",
	};
	const color = status_color[frm.doc.status] || "grey";
	frm.page.set_indicator(frm.doc.status, color);
}


function _start_qr_poll(frm) {
	if (frm._qr_poll) return;
	frm._qr_poll = setInterval(() => {
		if (!frm.doc.name || frm.is_new()) {
			_stop_qr_poll(frm);
			return;
		}
		frappe.call({
			method: "frappe_whatsapp_openwa.api.session.get_status",
			args: { session_name: frm.doc.name },
			callback(r) {
				if (!r.message) return;
				const { status, qr_code_data } = r.message;
				if (status && status !== frm.doc.status) {
					frm.reload_doc();
				} else if (status === "QR Required" && qr_code_data && qr_code_data !== frm.doc.qr_code_data) {
					// QR codes rotate every ~20s — swap the image without a full reload.
					frm.doc.qr_code_data = qr_code_data;
					_render_qr(frm);
				}
			},
		});
	}, 8_000);
}


function _stop_qr_poll(frm) {
	if (frm._qr_poll) {
		clearInterval(frm._qr_poll);
		frm._qr_poll = null;
	}
}


function _subscribe_realtime(frm) {
	if (frm._realtime_handler) return;  // already subscribed for this form instance

	// Store the callback reference so we can remove exactly this handler on teardown.
	// Using a named function avoids removing other forms' listeners on the shared channel.
	frm._realtime_handler = function (data) {
		// Filter to this document only — the channel is site-wide.
		if (data.session_name !== frm.doc.name) return;
		if (data.status !== frm.doc.status) {
			frappe.show_alert({
				message: __("Session status changed to {0}", [data.status]),
				indicator: data.requires_human_attention ? "red" : "green",
			}, 5);
			frm.reload_doc();
		}
	};
	frappe.realtime.on("openwa_session_state_change", frm._realtime_handler);
}


function _unsubscribe_realtime(frm) {
	if (frm._realtime_handler) {
		// Pass the exact callback reference so only THIS form's listener is removed.
		frappe.realtime.off("openwa_session_state_change", frm._realtime_handler);
		frm._realtime_handler = null;
	}
}
