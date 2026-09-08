"""Delete the gateway-wide webhook secret.

Every session signs its deliveries with its own secret now, so a single key that
would still verify any session guarded nothing and would have exposed all of
them at once if leaked. The field is gone from the doctype; this removes the
value, because a credential does not get to sit in the database as an orphaned
Singles row after the field that owned it has been removed.

Separate from per_session_webhook_secrets because that patch has already run on
existing sites — a patch is executed once, so the cleanup needs its own entry to
reach installs that upgraded earlier.
"""

import frappe

_DOCTYPE = "OpenWA Gateway Settings"
_FIELD = "webhook_secret"


def execute():
	if frappe.db.table_exists("Singles"):
		frappe.db.delete("Singles", {"doctype": _DOCTYPE, "field": _FIELD})

	# Password fields keep their value in __Auth rather than Singles.
	try:
		frappe.db.delete("__Auth", {"doctype": _DOCTYPE, "fieldname": _FIELD})
	except Exception:
		frappe.log_error(
			title="OpenWA: could not clear the shared webhook secret from __Auth",
			message=frappe.get_traceback(),
		)

	frappe.clear_cache(doctype=_DOCTYPE)
	frappe.db.commit()
