// Gateway health, on the document that governs the gateway.
//
// The metrics endpoint existed and computed sessions, queue depth and 24-hour
// send counts, but nothing in the Desk ever called it: the numbers were only
// reachable by hand-calling the method. This is where someone looks when they
// want to know whether the gateway is coping.

frappe.ui.form.on("OpenWA Gateway Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Gateway Health"), () => show_health(frm));
	},
});

function show_health(frm) {
	frappe.call({
		method: "frappe_whatsapp_openwa.monitoring.metrics.get_gateway_metrics",
		freeze: true,
		freeze_message: __("Reading gateway health…"),
		callback: (r) => {
			if (!r.message) {
				return;
			}
			const dialog = new frappe.ui.Dialog({
				title: __("Gateway Health"),
				size: "large",
				fields: [{ fieldtype: "HTML", fieldname: "health" }],
				primary_action_label: __("Close"),
				primary_action() {
					this.hide();
				},
			});
			dialog.fields_dict.health.$wrapper.append(render(r.message));
			dialog.show();
		},
	});
}

function render(m) {
	const sessions = m.sessions || {};
	const queue = m.queue || {};
	const messages = (m.messages || {}).last_24h || {};

	const rows = (obj) =>
		Object.keys(obj).length
			? Object.entries(obj)
					.map(
						([k, v]) =>
							`<tr><td>${frappe.utils.escape_html(String(k))}</td>
							 <td class="text-right">${frappe.utils.escape_html(String(v))}</td></tr>`
					)
					.join("")
			: `<tr><td colspan="2" class="text-muted">${__("Nothing recorded")}</td></tr>`;

	const table = (heading, obj) => `
		<div class="mb-4">
			<h6 class="text-uppercase text-muted">${heading}</h6>
			<table class="table table-bordered table-sm mb-0">
				<tbody>${rows(obj)}</tbody>
			</table>
		</div>`;

	return $(`
		<div class="p-3">
			${table(__("Sessions ({0} total)", [sessions.total || 0]), sessions.by_status || {})}
			${table(__("Outbound queue ({0} total)", [queue.total || 0]), queue.by_status || {})}
			${table(__("Messages sent in the last 24 hours, by provider"), messages)}
		</div>
	`);
}
