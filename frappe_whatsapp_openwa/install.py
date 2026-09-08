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
	_release_gateway_sessions()

	for role in _APP_ROLES:
		if frappe.db.exists("Role", role):
			try:
				# force=True also clears the associated Has Role assignments.
				frappe.delete_doc("Role", role, force=True, ignore_permissions=True)
			except Exception:
				frappe.log_error(title=f"frappe_whatsapp_openwa uninstall: failed to delete Role {role}")
	frappe.db.commit()


def _release_gateway_sessions():
	"""Delete this site's sessions on the OpenWA gateway before the app goes.

	Uninstall drops the OpenWA Session table outright — it does not delete the
	documents, so on_trash never runs and the gateway is never told. Every
	session this site created would keep running there with nothing left that
	could reconnect, reuse or remove it: the same stranded-session problem a
	single delete has, multiplied by the whole site.

	Best effort, one session at a time: an unreachable gateway must not be able
	to block an uninstall, and one session that will not release must not stop
	the others from being released.
	"""
	from frappe_whatsapp_openwa.api.provision import release_gateway_session

	try:
		sessions = frappe.get_all(
			"OpenWA Session",
			filters={"gateway_session_id": ["is", "set"]},
			fields=["name", "gateway_session_id"],
		)
	except Exception:
		return

	for session in sessions:
		try:
			release_gateway_session(session.name, session.gateway_session_id)
		except Exception:
			frappe.log_error(
				title=f"frappe_whatsapp_openwa uninstall: could not release {session.name}",
				message=frappe.get_traceback(),
			)


def after_migrate():
	"""Repair anything migrate can repair, so a save cannot fail on an import.

	A standard Notification whose module file is missing does not just skip the
	alert — it raises inside the document save that triggered it. Writing the
	missing files here means that cannot happen because of a file.
	"""
	from frappe_whatsapp_openwa.utils.standard_notifications import ensure_importable

	result = ensure_importable(["WhatsApp Dual Gateway"])

	if result["repaired"]:
		frappe.log_error(
			title="OpenWA: repaired standard notification modules",
			message=(
				"Created the missing module files for: "
				+ ", ".join(result["repaired"])
				+ ". Restart the bench so running workers pick them up — Python "
				"caches a failed import, and migrate does not restart workers."
			),
		)

	if result["demoted"]:
		frappe.log_error(
			title="OpenWA: demoted orphaned standard notifications",
			message=(
				"These Notifications were marked standard but this release does not "
				"ship a definition for them: "
				+ ", ".join(result["demoted"])
				+ ". They are now ordinary Notifications — they still run, and they "
				"no longer fail the saves they are attached to. Delete them if they "
				"are left over from an older release."
			),
		)
