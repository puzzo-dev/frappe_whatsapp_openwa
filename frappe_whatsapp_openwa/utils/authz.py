"""Object-level authorisation for the whitelisted send endpoints.

The send endpoints take the sending `WhatsApp Account` as a client-supplied
argument, but historically only checked `create` on the `WhatsApp Message`
doctype. That check is doctype-wide: it says the caller may send *a* message,
not that they may send *as this account*. On a single-company site the two
coincide, because the shipped permission rows give `WhatsApp Message` create
only to System Manager, who can read every account anyway.

They stop coinciding on a multi-company site — which this app explicitly
supports: it adds a `custom_company` Link on `WhatsApp Account` and picks the
outgoing account by company (see overrides/notification.py). As soon as an
administrator grants `WhatsApp Message` create to a company-scoped role, the
doctype-wide check lets a user in Company A send on Company B's account, with
the message and its cost attributed to B.

`frappe.has_permission(..., doc=...)` is the fix rather than a hand-rolled
company comparison: because `custom_company` is an ordinary Link to Company
with user permissions left enabled, a User Permission on Company already
restricts which `WhatsApp Account` rows a user may read. Checking read on the
specific account therefore enforces the company boundary the site has actually
configured, and stays correct if that configuration changes.
"""

import frappe


def assert_can_send_from_account(account_name: str) -> None:
	"""Throw unless the caller is allowed to send as `account_name`.

	Callers must still check `create` on `WhatsApp Message` — this is the
	object-level half of the check, not a replacement for it.
	"""
	if not account_name:
		frappe.throw(
			frappe._("A WhatsApp Account is required to send a message."),
			frappe.ValidationError,
		)

	if not frappe.db.exists("WhatsApp Account", account_name):
		# Same message as the permission failure below: distinguishing the two
		# would let a caller probe which account names exist.
		raise frappe.PermissionError(
			frappe._("Not permitted to send from WhatsApp Account {0}").format(account_name)
		)

	if not frappe.has_permission("WhatsApp Account", "read", doc=account_name):
		raise frappe.PermissionError(
			frappe._("Not permitted to send from WhatsApp Account {0}").format(account_name)
		)
