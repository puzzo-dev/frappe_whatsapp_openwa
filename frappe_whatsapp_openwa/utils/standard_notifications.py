"""Keep standard Notifications importable, without inventing ones we don't ship.

Frappe imports a Python module for every Notification marked `is_standard`
before it sends one — `<app>/<module>/notification/<name>/<name>.py`. If that
import fails the whole thing throws, and because the exception surfaces inside
`run_notifications` during `on_change`, it does not merely skip the alert: it
fails the document save that triggered it. A notification on a Value Change of
OpenWA Session therefore broke every save of a session, including the status
poll the form makes on its own.

The app is the source of truth for its standard Notifications: what it ships in
`notification/` is the complete set. So the repair runs in that direction —
over the folders on disk, filling in the package files a partial checkout or a
restored backup might be missing.

It deliberately does not run the other way. Writing a folder for a database
record the release does not ship exports that record back into the app source,
where `frappe.model.sync` finds it on the next migrate and re-creates it — so a
notification the release dropped comes back, permanently, with the app now
carrying a file for it. A standard record with no shipped definition is an
orphan from an older release or from developer mode; it is demoted to
non-standard instead, which stops the failing import at once, leaves the alert
working (a non-standard Notification needs no module file), and deletes nothing.

None of this helps a process that already tried the import and cached the miss;
Python remembers a failed lookup, so a worker started before the files existed
keeps failing until it restarts. `bench migrate` does not restart workers, which
is why a release that adds a new Python subpackage needs one.
"""

import importlib
import os

import frappe


def ensure_importable(modules: list[str]) -> dict[str, list[str]]:
	"""Repair the shipped notification packages and demote orphaned records.

	Returns {"repaired": [...], "demoted": [...]} — both in
	"<module>/<slug>" form, for the caller to report.
	"""
	repaired: list[str] = []
	demoted: list[str] = []

	for module in modules:
		try:
			module_path = frappe.get_module_path(module)
		except Exception:
			continue

		base = os.path.join(module_path, "notification")
		shipped = _shipped_slugs(base)

		if shipped:
			_ensure_package(base)

		for slug in shipped:
			folder = os.path.join(base, slug)
			_ensure_package(folder)

			# The module Frappe imports. Empty is fine — it exists so that
			# `get_doc_module` resolves; the behaviour lives in the JSON.
			leaf = os.path.join(folder, f"{slug}.py")
			if not os.path.exists(leaf):
				with open(leaf, "w"):
					pass
				repaired.append(f"{module}/{slug}")

		for name in _standard_notifications(module):
			if frappe.scrub(name) in shipped:
				continue
			frappe.db.set_value("Notification", name, "is_standard", 0, update_modified=False)
			demoted.append(f"{module}/{name}")

	if repaired:
		# So this process picks up what was just written.
		importlib.invalidate_caches()

	return {"repaired": repaired, "demoted": demoted}


def _shipped_slugs(base: str) -> set[str]:
	"""Notification folders the app actually ships — those carrying a definition.

	A folder with only package files is residue from the export this module used
	to perform; it defines nothing, so it does not count as shipped.
	"""
	if not os.path.isdir(base):
		return set()
	return {
		entry
		for entry in os.listdir(base)
		if os.path.isfile(os.path.join(base, entry, f"{entry}.json"))
	}


def _standard_notifications(module: str) -> list[str]:
	try:
		return frappe.get_all(
			"Notification", filters={"module": module, "is_standard": 1}, pluck="name"
		)
	except Exception:
		return []


def _ensure_package(path: str) -> None:
	os.makedirs(path, exist_ok=True)
	init = os.path.join(path, "__init__.py")
	if not os.path.exists(init):
		with open(init, "w"):
			pass
