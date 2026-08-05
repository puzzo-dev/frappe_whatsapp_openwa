"""Remap removed OpenWA Session statuses to the simplified set.

"Restart Failed" and "Banned" no longer exist as desk statuses — both are
terminal states the operator must act on, so they fold into "Failed".
"""

import frappe


def execute():
	frappe.db.sql(
		"""
		UPDATE `tabOpenWA Session`
		SET status = 'Failed'
		WHERE status IN ('Restart Failed', 'Banned')
		"""
	)
