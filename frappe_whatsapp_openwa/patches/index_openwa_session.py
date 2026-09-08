"""Index the OpenWA Session columns the hot paths filter on.

add_performance_indexes covered the log and queue tables and missed the session
table itself, which every send and every inbound event goes through:

  linked_whatsapp_account — the resolver lists an account's sessions to choose
                            which one carries each message.

gateway_session_id and status are already indexed: their field definitions
carry search_index, so Frappe creates them. Indexing them again here only added
a duplicate.

add_index is a no-op when the index already exists, so this is safe to re-run
and cannot conflict with the on_doctype_update declaration.
"""

import frappe

# gateway_session_id and status already carry search_index on the field, so
# Frappe indexes them itself. Adding them again produced a second index on the
# same column — pure write cost and disk for no read benefit. Only the column
# the field definitions miss is added here.
_INDEXES = [
	("OpenWA Session", ["linked_whatsapp_account"]),
]


def execute():
	for doctype, fields in _INDEXES:
		if not frappe.db.table_exists(doctype):
			continue
		try:
			frappe.db.add_index(doctype, fields)
		except Exception:
			frappe.log_error(
				title=f"OpenWA: could not add index on {doctype} {fields}",
				message=frappe.get_traceback(),
			)
