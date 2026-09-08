"""Bulk WhatsApp Message controller subclass — campaign-level OpenWA routing.

Registered via hooks.py:
    override_doctype_class = {
        "Bulk WhatsApp Message":
            "frappe_whatsapp_openwa.overrides.bulk_whatsapp_message.BulkWhatsAppMessageDualGateway"
    }

The campaign chooses which OpenWA sessions carry it and how they are picked.
Nothing is stamped onto the individual messages: each one already carries
`bulk_message_reference`, and WhatsAppMessageDualGateway reads the choice back
from the campaign at send time, when the sessions' live rate and cap are known.

Import guard mirrors overrides/whatsapp_message.py: the upstream class is
imported defensively so a restructure of frappe_whatsapp cannot make this
module unimportable, which would break every campaign save site-wide.
"""

from __future__ import annotations

import frappe

try:
	from frappe_whatsapp.frappe_whatsapp.doctype.bulk_whatsapp_message.bulk_whatsapp_message import (
		BulkWhatsAppMessage as _UpstreamBase,
	)
except ImportError:  # pragma: no cover - only when frappe_whatsapp is absent
	from frappe.model.document import Document as _UpstreamBase  # type: ignore[assignment]


class BulkWhatsAppMessageDualGateway(_UpstreamBase):
	def validate(self):
		super().validate()
		self._authorize_account()
		self._validate_openwa_routing()

	def on_update(self):
		on_update = getattr(super(), "on_update", None)
		if on_update:
			on_update()
		self._clear_routing_cache()

	def on_submit(self):
		# The cache is keyed on the campaign name and read by every send. Clear
		# it here as well as on_update: on_update does not fire for the submit
		# itself, so without this the first messages of a campaign could route
		# on values cached from a draft save.
		self._clear_routing_cache()
		super().on_submit()

	# ── Validation ───────────────────────────────────────────────────────

	def _authorize_account(self):
		"""A campaign sends as an account, so it takes the same check a send does.

		The session and account ownership rules below govern which sessions may
		carry the campaign; none of them asked whether this user may send as the
		account at all, so a company-scoped user could point a campaign at
		another company's account and have the traffic and cost attributed
		there.
		"""
		if self.flags.ignore_permissions or not self.whatsapp_account:
			return

		from frappe_whatsapp_openwa.utils.authz import assert_can_send_from_account

		assert_can_send_from_account(self.whatsapp_account)

	def _validate_openwa_routing(self):
		rows = self.get("custom_openwa_sessions") or []
		if not rows and not (self.get("custom_routing_mode") or "").strip():
			return

		self._dedupe_sessions(rows)
		rows = self.get("custom_openwa_sessions") or []
		mode = (self.get("custom_routing_mode") or "").strip()

		if rows and not self.whatsapp_account:
			frappe.throw(
				frappe._("Select a WhatsApp Account before choosing the sessions to send from."),
				title=frappe._("OpenWA Routing"),
			)

		if rows:
			self._validate_sessions_belong_to_account(rows)

		# Account-level means one session carries everything. Listing several
		# and then picking a mode that uses one of them is a contradiction the
		# sender would only discover by reading the sent messages.
		if mode == "Account-level" and len(rows) > 1:
			frappe.throw(
				frappe._(
					"Account-level routing sends the whole campaign from a single session, "
					"but {0} are selected. Keep one session, or choose Message-level or "
					"Hybrid to spread the campaign across all of them."
				).format(len(rows)),
				title=frappe._("OpenWA Routing"),
			)

		if self.whatsapp_account and not self._account_uses_openwa():
			frappe.msgprint(
				frappe._(
					"{0} does not send over the OpenWA gateway, so this campaign's routing "
					"choice will not be used. It takes effect if the account is switched to "
					"OpenWA before the campaign is submitted."
				).format(self.whatsapp_account),
				title=frappe._("OpenWA Routing"),
				indicator="orange",
			)

	def _dedupe_sessions(self, rows):
		seen, kept = set(), []
		for row in rows:
			if not row.openwa_session or row.openwa_session in seen:
				continue
			seen.add(row.openwa_session)
			kept.append(row)
		if len(kept) != len(rows):
			self.set("custom_openwa_sessions", kept)

	def _validate_sessions_belong_to_account(self, rows):
		names = [row.openwa_session for row in rows]
		linked = dict(
			frappe.get_all(
				"OpenWA Session",
				filters={"name": ["in", names]},
				fields=["name", "linked_whatsapp_account"],
				as_list=True,
			)
		)
		stray = [n for n in names if linked.get(n) != self.whatsapp_account]
		if stray:
			frappe.throw(
				frappe._(
					"These sessions are not linked to {0}, so they cannot send this campaign: {1}"
				).format(self.whatsapp_account, ", ".join(stray)),
				title=frappe._("OpenWA Routing"),
			)

	def _account_uses_openwa(self) -> bool:
		try:
			return (
				frappe.db.get_value(
					"WhatsApp Account Provider Extension", self.whatsapp_account, "default_provider"
				)
				== "OpenWA"
			)
		except Exception:
			# Never let a settings read block saving a campaign.
			return True

	# ── Cache ────────────────────────────────────────────────────────────

	def _clear_routing_cache(self):
		from frappe_whatsapp_openwa.routing.campaign import clear_campaign_routing

		clear_campaign_routing(self.name)
