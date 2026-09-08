"""Keep standard Notifications importable.

Frappe imports a Python module for every Notification marked `is_standard`
before it sends one — `<app>/<module>/notification/<name>/<name>.py`. If that
import fails the whole thing throws, and because the exception surfaces inside
`run_notifications` during `on_change`, it does not merely skip the alert: it
fails the document save that triggered it. A notification on a Value Change of
OpenWA Session therefore broke every save of a session, including the status
poll the form makes on its own.

Files can go missing in ordinary ways — a Notification created in developer
mode is marked standard but its folder is only written on save, a partial
checkout, a record restored from a backup without the app files. So rather than
trust that they are there, migrate checks and writes whatever is absent.

This does not help a process that already tried the import and cached the miss;
Python remembers a failed lookup, so a worker started before the files existed
keeps failing until it restarts. `bench migrate` does not restart workers, which
is why a release that adds a new Python subpackage needs one.
"""

import importlib
import os

import frappe


def ensure_importable(modules: list[str]) -> list[str]:
	"""Create any missing notification package files. Returns what was repaired."""
	repaired = []

	for module in modules:
		try:
			module_path = frappe.get_module_path(module)
		except Exception:
			continue

		names = frappe.get_all(
			"Notification",
			filters={"module": module, "is_standard": 1},
			pluck="name",
		)
		if not names:
			continue

		base = os.path.join(module_path, "notification")
		_ensure_package(base)

		for name in names:
			slug = frappe.scrub(name)
			folder = os.path.join(base, slug)
			_ensure_package(folder)

			# The module Frappe imports. Empty is fine — it exists so that
			# `get_doc_module` resolves; the behaviour lives in the JSON.
			leaf = os.path.join(folder, f"{slug}.py")
			if not os.path.exists(leaf):
				with open(leaf, "w"):
					pass
				repaired.append(f"{module}/{slug}")

			# The definition itself. Only written when absent: migrate syncs
			# JSON into the database, so exporting unconditionally would push
			# the database back over a definition someone had just changed in
			# git. Missing is the one case where the database is the only copy.
			if not os.path.exists(os.path.join(folder, f"{slug}.json")):
				try:
					from frappe.modules.export_file import export_to_files

					export_to_files(
						record_list=[["Notification", name]],
						record_module=module,
						create_init=True,
					)
					repaired.append(f"{module}/{slug}.json")
				except Exception:
					frappe.log_error(
						title=f"OpenWA: could not export notification {name}",
						message=frappe.get_traceback(),
					)

	if repaired:
		# So this process picks up what was just written.
		importlib.invalidate_caches()

	return repaired


def _ensure_package(path: str) -> None:
	os.makedirs(path, exist_ok=True)
	init = os.path.join(path, "__init__.py")
	if not os.path.exists(init):
		with open(init, "w"):
			pass
