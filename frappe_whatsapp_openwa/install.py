"""Install / uninstall lifecycle for frappe_whatsapp_openwa.

Frappe's uninstall automatically removes records whose `module` field matches
this app's module ("WhatsApp Dual Gateway") — that covers our Custom Fields
and every DocType this app owns.

Roles have no `module` link, so they are never swept by the module-based
uninstall and must be removed explicitly here to avoid orphan residue.
"""

import frappe

_APP_ROLES = ("OpenWA Manager",)


def before_uninstall():
	"""Remove app-owned artefacts that Frappe's module-based uninstall leaves behind."""
	for role in _APP_ROLES:
		if frappe.db.exists("Role", role):
			try:
				# force=True also clears the associated Has Role assignments.
				frappe.delete_doc("Role", role, force=True, ignore_permissions=True)
			except Exception:
				frappe.log_error(title=f"frappe_whatsapp_openwa uninstall: failed to delete Role {role}")
	frappe.db.commit()
