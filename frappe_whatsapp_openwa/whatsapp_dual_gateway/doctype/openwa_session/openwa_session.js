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
		if (frm.doc.status === "Banned") {
			frappe.show_alert({
				message: __("This session has been banned. A SIM swap is required before it can reconnect."),
				indicator: "red",
			}, 12);
		}
	},

	before_unload(frm) {
		_stop_qr_poll(frm);
		_unsubscribe_realtime(frm);
	},
});


function _render_qr(frm) {
	const wrapper = frm.fields_dict.qr_code_data && frm.fields_dict.qr_code_data.wrapper;
	if (!wrapper) return;

	if (frm.doc.status === "QR Required" && frm.doc.qr_code_data) {
		frm.set_intro(
			__("Scan the QR code with the WhatsApp paired phone within 60 seconds."),
			"orange"
		);
		const raw = frm.doc.qr_code_data;
		const src = raw.startsWith("data:") ? raw : `data:image/png;base64,${raw}`;
		$(wrapper).empty().append(`
			<div style="text-align:center;padding:20px">
				<img src="${src}"
				     style="max-width:260px;border:2px solid #ff9800;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,0.12)"/>
				<p style="margin-top:10px;color:#666;font-size:12px">
					${__("WhatsApp → Linked Devices → Link a Device")}
				</p>
			</div>`);
		_start_qr_poll(frm);
	} else {
		$(wrapper).empty();
		_stop_qr_poll(frm);
		if (frm.doc.status === "QR Required") {
			frm.set_intro(__("Waiting for QR code from gateway…"), "blue");
		} else {
			frm.set_intro("");
		}
	}
}


function _add_buttons(frm) {
	frm.clear_custom_buttons();
	if (frm.is_new()) return;

	frm.add_custom_button(__("Refresh Status"), () => frm.reload_doc());

	if (!frm.doc.gateway_session_id) {
		frm.add_custom_button(__("Initialize on Gateway"), () => {
			frappe.confirm(
				__("Register this session with the OpenWA gateway? The gateway will push a QR code once ready."),
				() => {
					frappe.call({
						method: "frappe_whatsapp_openwa.api.provision.provision_session",
						args: { session_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Registering with gateway…"),
						callback(r) {
							if (r.message) {
								frappe.show_alert({
									message: __("Session registered. Waiting for QR code…"),
									indicator: "blue",
								}, 6);
								frm.reload_doc();
							}
						},
					});
				}
			);
		}, __("Actions"));
	}

	if (frm.doc.gateway_session_id && (frm.doc.status === "Initializing" || frm.doc.status === "QR Required")) {
		frm.add_custom_button(__("Request QR"), () => {
			frappe.call({
				method: "frappe_whatsapp_openwa.api.session.get_qr",
				args: { session_name: frm.doc.name },
				callback(r) {
					if (r.message) {
						frm.set_value("qr_code_data", r.message.qr_code_data);
						frm.set_value("status", r.message.status);
					}
				},
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

	if (["Disconnected", "Restart Failed"].includes(frm.doc.status)) {
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
		Banned: "red",
		"Restart Failed": "red",
	};
	const color = status_color[frm.doc.status] || "grey";
	frm.page.set_indicator(frm.doc.status, color);
}


function _start_qr_poll(frm) {
	if (frm._qr_poll) return;
	frm._qr_poll = setInterval(() => {
		if (!frm.doc.name || frm.doc.status !== "QR Required") {
			_stop_qr_poll(frm);
			return;
		}
		frappe.call({
			method: "frappe_whatsapp_openwa.api.session.get_status",
			args: { session_name: frm.doc.name },
			callback(r) {
				if (!r.message) return;
				const { status } = r.message;
				if (status && status !== frm.doc.status) {
					frm.reload_doc();
				}
			},
		});
	}, 10_000);
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
