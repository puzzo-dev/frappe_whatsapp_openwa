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


// Frappe's set_intro appends: form/layout.js does `$html.appendTo(this.message)`
// rather than replacing, so calling it again stacks another banner. _render_qr
// runs on every form refresh and every QR rotation, which is why the scan
// instructions piled up down the screen. Only speak when the message changes.
function _set_intro(frm, text, color) {
	if (frm._openwa_intro === text) return;
	frm._openwa_intro = text;
	frm.set_intro("");
	if (text) frm.set_intro(text, color);
}


function _render_qr(frm) {
	const wrapper = frm.fields_dict.qr_code_data && frm.fields_dict.qr_code_data.wrapper;

	if (frm.doc.status === "QR Required" && frm.doc.qr_code_data) {
		_set_intro(frm, __("Scan the QR code with the WhatsApp phone: WhatsApp → Linked Devices → Link a Device. The code refreshes automatically."), "orange");
		if (wrapper) {
			const raw = String(frm.doc.qr_code_data || "");
			// Gateway-supplied. Accept only a base64 payload or a data:image
			// URL — anything else could carry a quote, escape the src attribute
			// and become an onerror handler in the desk.
			const is_data_image = /^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+$/.test(raw);
			const is_bare_base64 = /^[A-Za-z0-9+/=\s]+$/.test(raw);
			if (!is_data_image && !is_bare_base64) {
				$(wrapper).empty();
				_set_intro(frm, __("The gateway returned an unreadable QR code."), "red");
				return;
			}
			const src = is_data_image ? raw : `data:image/png;base64,${raw}`;
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
			_set_intro(frm, __("Waiting for QR code from gateway…"), "blue");
			_start_qr_poll(frm);
		} else if (!frm.is_new() && frm.doc.status === "Initializing") {
			const msg = frm.doc.gateway_session_id
				? __("Waiting for the gateway engine to start… the QR code will appear here shortly.")
				: __("Provisioning this session on the gateway… the QR code will appear here shortly.");
			_set_intro(frm, msg, "blue");
			_start_qr_poll(frm);
		} else {
			_stop_qr_poll(frm);
			_set_intro(frm, "");
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

	// Manual connect via phone number + pairing code.
	// Visible once provisioned (gateway_session_id set) and not yet authenticated.
	if (frm.doc.gateway_session_id && frm.doc.status !== "Connected") {
		frm.add_custom_button(__("Connect with Phone Number"), () => {
			_show_pairing_code_dialog(frm);
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


// How long a realtime message is taken as proof the socket is working. Longer
// than the ~20s QR rotation would risk sitting on a stale code if the socket
// dropped straight after a message.
const _REALTIME_TRUST_MS = 15_000;


function _start_qr_poll(frm) {
	if (frm._qr_poll) return;
	frm._qr_poll = setInterval(() => {
		if (!frm.doc.name || frm.is_new()) {
			_stop_qr_poll(frm);
			return;
		}
		// Realtime pushes both status changes and QR rotations, so while it is
		// demonstrably delivering there is nothing for this tick to find — it
		// would only repeat the round trip and race the same reload_doc.
		// The poll stays as the fallback for a bench with socketio down, where
		// _realtime_seen_at never updates and every tick runs as before.
		if (frm._realtime_seen_at && Date.now() - frm._realtime_seen_at < _REALTIME_TRUST_MS) {
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

		// Proof that realtime is alive. The poll below reads this and stands
		// down while it holds, so the two stop doing the same work.
		frm._realtime_seen_at = Date.now();

		if (data.status !== frm.doc.status) {
			frappe.show_alert({
				message: __("Session status changed to {0}", [data.status]),
				indicator: data.requires_human_attention ? "red" : "green",
			}, 5);
			frm.reload_doc();
			return;
		}

		// Same status, new QR: the code rotates every ~20s and this event already
		// carries the new one, so swap the image instead of leaving it to the poll.
		if (data.qr_code_data && data.qr_code_data !== frm.doc.qr_code_data) {
			frm.doc.qr_code_data = data.qr_code_data;
			_render_qr(frm);
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


function _show_pairing_code_dialog(frm) {
	// Pre-fill with the session's own phone number if present.
	const default_phone = frm.doc.phone_number || "";

	const dialog = new frappe.ui.Dialog({
		title: __("Connect with Phone Number"),
		fields: [
			{
				fieldtype: "HTML",
				options: `<div class="alert alert-info" style="margin-bottom:12px;">
					<p>${__("Request a pairing code, then enter it on the phone under:")}</p>
					<p><strong>${__("WhatsApp → Settings → Linked Devices → Link with phone number")}</strong></p>
				</div>`,
			},
			{
				fieldtype: "Data",
				fieldname: "phone_number",
				label: __("Phone Number (international, digits only)"),
				default: default_phone,
				description: __("6–15 digits, no + or spaces. e.g. 2348012345678"),
				reqd: 1,
			},
			{
				fieldtype: "HTML",
				fieldname: "result_area",
			},
		],
		primary_action_label: __("Request Pairing Code"),
		primary_action: () => {
			const phone = dialog.get_value("phone_number");
			if (!phone) {
				frappe.msgprint(__("Enter a phone number."));
				return;
			}
			frappe.call({
				method: "frappe_whatsapp_openwa.api.session.request_pairing_code",
				args: { session_name: frm.doc.name, phone_number: phone },
				freeze: true,
				freeze_message: __("Requesting pairing code from gateway…"),
				callback(r) {
					if (!r.message) return;
					const code = r.message.pairing_code;
					const instructions = r.message.instructions;
					const area = dialog.get_field("result_area").$wrapper;
					// Escaped: both values originate in the gateway response,
					// which is an external system this desk page must not trust
					// with raw markup.
					area.html(`
						<div style="text-align:center;padding:20px;border:2px solid #ff9800;border-radius:8px;margin-top:12px;">
							<p style="font-size:13px;color:#6b7280;margin-bottom:8px;">${__("Your pairing code")}</p>
							<p style="font-size:32px;font-weight:700;letter-spacing:4px;color:#1e293b;margin:0 0 12px;">${frappe.utils.escape_html(code || "")}</p>
							<p style="font-size:12px;color:#6b7280;">${frappe.utils.escape_html(instructions || "")}</p>
						</div>
					`);
				},
			});
		},
	});
	dialog.show();
}
