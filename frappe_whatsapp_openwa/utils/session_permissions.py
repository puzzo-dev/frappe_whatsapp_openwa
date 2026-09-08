"""Company scoping for OpenWA Session.

The doctype carries a `company` Link and nothing enforced it: an OpenWA Manager
in one company could list, open and send from another company's numbers. The
field looked like a boundary and was decoration.

Frappe applies Company User Permissions to a Link field on its own for
*single-document* access, which is why there is no has_permission hook here —
what it does not do is constrain a list query, so that is what this supplies. A
session with no company set stays visible to everyone, matching how the rest of
this app treats an unset company: a restriction nobody configured is not one.
"""

import frappe


def get_openwa_session_permission_query_conditions(user=None):
	if user is None:
		user = frappe.session.user

	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return ""

	companies = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Company"},
		pluck="for_value",
	)
	if not companies:
		return ""

	allowed = ", ".join(frappe.db.escape(c) for c in companies)
	return (
		f"(`tabOpenWA Session`.`company` IN ({allowed})"
		f" OR `tabOpenWA Session`.`company` IS NULL"
		f" OR `tabOpenWA Session`.`company` = '')"
	)
