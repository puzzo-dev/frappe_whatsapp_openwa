"""Canonicalise session phone numbers before uniqueness is enforced.

One WhatsApp number drives one session, but the constraint is on the stored
string. Sites created before this could hold +2348012345678, 08012345678 and
2348012345678 as three separate sessions, all pointed at the same account.

This runs pre-model-sync, before the unique index is added, because adding that
index to a column that still holds duplicates fails the migration outright.

Duplicates that survive normalisation are *not* resolved here. Deleting a
session is not this patch's decision — one of them may be the live, connected
one — so it stops with the conflicts named and leaves the choice to an operator.
"""

import frappe


def execute():
	if not frappe.db.table_exists("OpenWA Session"):
		return

	from frappe_whatsapp_openwa.utils.phone import normalise_e164

	for row in frappe.db.sql(
		"SELECT name, phone_number FROM `tabOpenWA Session` WHERE IFNULL(phone_number, '') != ''",
		as_dict=True,
	):
		try:
			canonical = normalise_e164(row.phone_number)
		except Exception:
			continue
		if canonical and canonical != row.phone_number:
			frappe.db.set_value(
				"OpenWA Session", row.name, "phone_number", canonical, update_modified=False
			)

	frappe.db.commit()

	clashes = frappe.db.sql(
		"""SELECT phone_number, GROUP_CONCAT(name) AS sessions, COUNT(*) AS n
		   FROM `tabOpenWA Session`
		   WHERE IFNULL(phone_number, '') != ''
		   GROUP BY phone_number HAVING n > 1""",
		as_dict=True,
	)
	if clashes:
		detail = "; ".join(f"{c.phone_number}: {c.sessions}" for c in clashes)
		frappe.throw(
			"More than one OpenWA Session uses the same phone number, and a number can "
			"only drive one session. Delete or re-point the extra sessions, then run "
			f"migrate again — {detail}"
		)
