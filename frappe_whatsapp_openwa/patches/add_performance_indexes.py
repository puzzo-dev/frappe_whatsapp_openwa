"""Add the indexes the hot paths depend on.

Every one of these columns is filtered or ordered on by code that runs on a
schedule or on every inbound event, and none of them were indexed:

  WhatsApp Message.message_id          — looked up by every delivery ack
  WhatsApp Outbound Queue              — the worker's batch select, its stale
                                         reaper, and the weekly purge
  OpenWA Webhook Log                   — the purge, and the desk list filters
  WhatsApp Fallback Log.triggered_at   — the purge

The app-owned doctypes also declare these in `on_doctype_update`, which is the
right home for them, but that hook only fires when a DocType is actually
reloaded during migrate — so on an existing site it would not run and the
indexes would never appear. This patch closes that gap, and `add_index` is a
no-op when the index is already present, so the two cannot conflict.

`WhatsApp Message` belongs to upstream `frappe_whatsapp`, which this app does
not own; a patch is the supported way to index it from outside.
"""

import frappe

_INDEXES = [
	("WhatsApp Message", ["message_id"]),
	("WhatsApp Outbound Queue", ["status", "next_attempt_at", "enqueued_at"]),
	("WhatsApp Outbound Queue", ["status", "last_attempt_at"]),
	("WhatsApp Outbound Queue", ["status", "modified"]),
	("OpenWA Webhook Log", ["processed", "received_at"]),
	("OpenWA Webhook Log", ["session_id", "event_type"]),
	("WhatsApp Fallback Log", ["triggered_at"]),
]


def execute():
	for doctype, fields in _INDEXES:
		if not frappe.db.table_exists(doctype):
			continue
		try:
			frappe.db.add_index(doctype, fields)
		except Exception:
			# An index is an optimisation, never a correctness requirement:
			# a site that cannot create one (permissions, a row-size limit)
			# must still finish migrating.
			frappe.log_error(
				title=f"OpenWA: could not add index on {doctype} {fields}",
				message=frappe.get_traceback(),
			)
	frappe.db.commit()
