"""Bring the six alert Notifications onto this release's definition.

Until v1.1.0 they shipped as a fixture (``fixtures/notification.json``). They
now ship as Notifications in ``whatsapp_dual_gateway/notification/``, and the
changeover does not happen on its own:

  * ``import_file_by_path`` skips a file whose ``modified`` is not newer than
    the record in the database, and the fixture sync stamped those records with
    the time it ran. On any site that migrated after these definitions were
    written, the module folder is therefore never imported — verified on a live
    site, where a full ``bench migrate`` left a release-era record untouched,
    still carrying its old subject and ``is_standard = 0``.

  * The fixture file is deleted in this release. ``import_fixtures`` reads
    every JSON in the fixtures folder regardless of what the ``fixtures`` hook
    lists, so leaving it there would keep a second source of truth alive that
    overwrites the module folder on every migrate.

  * Folders under ``notification/`` that this release does not ship are
    removed. ``frappe.model.sync`` imports every JSON it finds there, so an
    orphan folder is not inert: it re-creates its record on every migrate.
    Older versions of the after-migrate repair wrote exactly such folders.

Forcing the import is safe here in a way it would not be on every migrate: this
runs once, and what it overwrites is a definition the app shipped, not one a
user wrote. A site that has deliberately customised an alert should make it
non-standard, which takes it out of the sync entirely.

``is_standard`` follows whatever the branch ships — 1 on the development
branches, 0 on the production ones so the app installs without developer mode.
The patch imports the file as it finds it and takes no view of its own.
"""

import os
import shutil

import frappe

MODULE = "WhatsApp Dual Gateway"

# What this release ships. Anything else under notification/ is residue.
SHIPPED = (
	"openwa_counter_reset_failed",
	"openwa_dead_letter_alert",
	"openwa_session_banned",
	"openwa_session_needs_attention",
	"openwa_session_recovered",
	"openwa_session_restart_failed",
)


def execute():
	try:
		base = os.path.join(frappe.get_module_path(MODULE), "notification")
	except Exception:
		return

	_remove_unshipped_folders(base)
	_force_import_shipped(base)


def _remove_unshipped_folders(base: str):
	"""Delete exported notification folders this release does not ship."""
	if not os.path.isdir(base):
		return

	for entry in sorted(os.listdir(base)):
		folder = os.path.join(base, entry)
		if not os.path.isdir(folder) or entry in SHIPPED or entry == "__pycache__":
			continue
		# Only touch what looks like an exported notification, so an unrelated
		# directory someone put here is never removed.
		if not any(
			os.path.isfile(os.path.join(folder, f"{entry}.{ext}")) for ext in ("json", "py")
		):
			continue
		try:
			shutil.rmtree(folder)
			frappe.log_error(
				title="OpenWA: removed orphaned notification folder",
				message=(
					f"{folder} defined a Notification this release does not ship. "
					"It was re-imported on every migrate, so the record it created "
					"could never stay deleted."
				),
			)
		except OSError:
			frappe.log_error(
				title=f"OpenWA: could not remove {folder}",
				message=frappe.get_traceback(),
			)


def _force_import_shipped(base: str):
	"""Import each shipped definition regardless of the timestamp comparison."""
	from frappe.modules.import_file import import_file_by_path

	for slug in SHIPPED:
		path = os.path.join(base, slug, f"{slug}.json")
		if not os.path.isfile(path):
			continue
		try:
			import_file_by_path(path, force=True, ignore_version=True)
		except Exception:
			frappe.log_error(
				title=f"OpenWA: could not import notification {slug}",
				message=frappe.get_traceback(),
			)

	frappe.db.commit()
