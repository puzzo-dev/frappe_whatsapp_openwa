"""Regression test for the migration abort on `tabSingles`.

The patch aborted a production migrate with

    pymysql.err.OperationalError: (1054, "Unknown column 'modified' in 'ORDER BY'")

`frappe.db.get_value` orders by "modified desc" unless told otherwise, and
`tabSingles` carries only doctype, field and value. Every unit test passed
because the suite stubs `frappe.db` with a MagicMock, and every local migrate
passed because the patch returns early when no OpenWA Session exists — so the
failing line was only ever reached on a site that had sessions.

This test creates a session first, which is what makes it a real test of that
line rather than of the early return.
"""

import frappe

from frappe_whatsapp_openwa.patches import move_limits_to_sessions
from frappe_whatsapp_openwa.tests_integration.compat import FrappeTestCase

_SESSION = "_test_move_limits_session"
_PHONE = "+10000000042"


class TestMoveLimitsToSessions(FrappeTestCase):
	def setUp(self):
		self._cleanup()
		frappe.get_doc(
			{
				"doctype": "OpenWA Session",
				"session_name": _SESSION,
				"phone_number": _PHONE,
				"status": "Initializing",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		self._cleanup()

	def _cleanup(self):
		frappe.db.delete("OpenWA Session", {"session_name": _SESSION})
		frappe.db.delete(
			"Singles", {"doctype": "OpenWA Gateway Settings", "field": "send_rate_max"}
		)
		frappe.db.commit()

	def test_reading_singles_does_not_order_by_modified(self):
		"""The exact query the patch makes must survive the real table.

		`tabSingles` has no `modified` column, so the default ordering is not a
		style preference here — it is the difference between a migration that
		completes and one that aborts partway through.
		"""
		frappe.db.sql(
			"""insert into `tabSingles` (doctype, field, value)
			   values ('OpenWA Gateway Settings', 'send_rate_max', '7')"""
		)
		frappe.db.commit()

		move_limits_to_sessions.execute()

		self.assertEqual(
			frappe.db.get_value("OpenWA Session", _SESSION, "send_rate_max"),
			7,
			"a value an operator had changed must land on the session",
		)

	def test_patch_runs_with_a_session_present(self):
		"""Guards the early return: with a session, the loop must execute."""
		self.assertTrue(frappe.db.exists("OpenWA Session", _SESSION))
		move_limits_to_sessions.execute()  # must not raise
