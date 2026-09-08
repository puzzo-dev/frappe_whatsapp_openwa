"""The repair never invents a notification the release does not ship.

Writing a folder for a database record exports it into the app source, where
frappe.model.sync finds it on the next migrate and re-creates the record — so a
notification a release removed comes back permanently, with the app now
carrying a file for it. The orphan is demoted instead: the failing import only
happens for standard records, so demotion stops it without deleting anything.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

from frappe_whatsapp_openwa.utils.standard_notifications import ensure_importable

_MOD = "frappe_whatsapp_openwa.utils.standard_notifications"


def _module_tree(shipped: list[str]) -> str:
	"""A module path whose notification/ folder ships exactly these slugs."""
	root = tempfile.mkdtemp()
	base = os.path.join(root, "notification")
	for slug in shipped:
		folder = os.path.join(base, slug)
		os.makedirs(folder)
		with open(os.path.join(folder, f"{slug}.json"), "w") as f:
			f.write("{}")
	return root


def _run(module_path, standard_names):
	import frappe as _frappe

	db = MagicMock()
	with patch.object(_frappe, "get_module_path", create=True, return_value=module_path), \
		patch.object(_frappe, "get_all", create=True, return_value=list(standard_names)), \
		patch.object(_frappe, "scrub", create=True, side_effect=lambda n: n.lower().replace(" ", "_").replace("-", "_")), \
		patch.object(_frappe, "db", db):
		result = ensure_importable(["Test Module"])
	return result, db


class TestShippedOnly:
	def test_missing_module_file_is_written_for_a_shipped_notification(self):
		root = _module_tree(["session_banned"])
		leaf = os.path.join(root, "notification", "session_banned", "session_banned.py")
		assert not os.path.exists(leaf)

		result, _ = _run(root, ["Session Banned"])

		assert os.path.exists(leaf)
		assert result["repaired"] == ["Test Module/session_banned"]

	def test_no_folder_is_created_for_an_unshipped_notification(self):
		"""The regression: an exported orphan is re-imported on every migrate."""
		root = _module_tree(["session_banned"])

		_run(root, ["Session Banned", "Retired Alert"])

		assert not os.path.exists(os.path.join(root, "notification", "retired_alert"))

	def test_an_unshipped_notification_is_demoted(self):
		root = _module_tree(["session_banned"])

		result, db = _run(root, ["Session Banned", "Retired Alert"])

		assert result["demoted"] == ["Test Module/Retired Alert"]
		db.set_value.assert_called_once_with(
			"Notification", "Retired Alert", "is_standard", 0, update_modified=False
		)

	def test_a_shipped_notification_is_never_demoted(self):
		root = _module_tree(["session_banned"])

		result, db = _run(root, ["Session Banned"])

		assert result["demoted"] == []
		assert not db.set_value.called

	def test_a_folder_without_a_definition_does_not_count_as_shipped(self):
		"""Residue from the old export defines nothing, so it is not the source of truth."""
		root = _module_tree([])
		os.makedirs(os.path.join(root, "notification", "retired_alert"))

		result, db = _run(root, ["Retired Alert"])

		assert result["demoted"] == ["Test Module/Retired Alert"]
		assert db.set_value.called
