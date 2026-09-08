"""WhatsApp Message controller subclass.

Registered via hooks.py:
    override_doctype_class = {
        "WhatsApp Message": "frappe_whatsapp_openwa.overrides.whatsapp_message.WhatsAppMessageDualGateway"
    }

For Meta-routed accounts: calls super().send_outgoing() unchanged.
For OpenWA-routed accounts: routes through the OpenWA adapter instead of Meta.

Import guard: the upstream class is imported lazily inside methods so that a
restructure of frappe_whatsapp does NOT prevent the module from loading at all
(which would make every WhatsApp Message insert fail site-wide).
"""

from __future__ import annotations

import frappe


def _get_upstream_class():
	"""Lazy import of upstream WhatsAppMessage with a clear error on failure."""
	try:
		from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
			WhatsAppMessage,
		)
		return WhatsAppMessage
	except ImportError as e:
		frappe.log_error(
			title="frappe_whatsapp_openwa: upstream WhatsAppMessage import failed",
			message=str(e),
		)
		raise


# Module-level class definition uses lazy base so the module itself can be
# imported even when the upstream package is temporarily absent.
try:
	from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
		WhatsAppMessage as _UpstreamBase,
	)
except ImportError:
	# Fallback to frappe.model.document.Document so the module loads;
	# send_outgoing will raise a clear error when actually called.
	from frappe.model.document import Document as _UpstreamBase  # type: ignore[assignment]


from frappe_whatsapp_openwa.utils.session_cap import release_slot, reserve_slot


class WhatsAppMessageDualGateway(_UpstreamBase):
	def send_outgoing(self):
		if self.type != "Outgoing":
			return

		# A message_id is assigned by the provider when the message is dispatched,
		# so a document that already carries one has already been sent and must
		# never be dispatched again.
		#
		# The message_type == "Template" qualifier that used to be here mirrored
		# upstream, where the guard sits on the template branch only
		# (`elif not self.message_id: self.send_template()`). That is safe
		# upstream because nothing there sends before insert. It is not safe
		# here: MetaAdapter dispatches via the upstream controller and *then*
		# calls insert(), so before_insert re-entered this method with a
		# message_id already set, and for a non-template message the qualifier
		# let it fall through and send to Meta a second time — a duplicate
		# message and a duplicate charge on every text and media send that
		# routed to Meta.
		#
		# Retries are unaffected: a send that failed never received a
		# message_id, so a Failed document still re-sends.
		if self.message_id:
			return

		# Ensure the upstream base is really WhatsAppMessage (not the fallback Document).
		if not hasattr(self, "_dual_gateway_base_verified"):
			try:
				_get_upstream_class()
			except ImportError:
				frappe.throw(
					"frappe_whatsapp_openwa: upstream WhatsAppMessage is unavailable. "
					"Please check that frappe_whatsapp is installed correctly.",
					title="Dual Gateway Error",
				)
			self._dual_gateway_base_verified = True  # type: ignore[attr-defined]

		if not self.whatsapp_account:
			super().send_outgoing()
			return

		self._check_meta_rate_limit(self.whatsapp_account)

		try:
			from frappe_whatsapp_openwa.routing.campaign import campaign_routing
			from frappe_whatsapp_openwa.routing.resolver import resolve_provider
			session_strategy = None
			if self.template:
				session_strategy = frappe.db.get_value(
					"WhatsApp Templates", self.template, "custom_session_strategy"
				) or None
			# A campaign chooses its own routing mode and the numbers it may send
			# from; the account's setting covers everything else. Read per message
			# rather than stamped at queue time, so the choice of session sees the
			# live send rate and daily cap instead of a guess made minutes earlier.
			mode_override, session_pool = campaign_routing(
				getattr(self, "bulk_message_reference", None)
			)
			provider, session_name = resolve_provider(
				self.whatsapp_account,
				None,
				session_strategy,
				mode_override=mode_override,
				session_pool=session_pool,
			)
		except Exception:
			frappe.log_error(
				title="DualGateway: resolver error — falling back to Meta",
				message=frappe.get_traceback(),
			)
			self._check_meta_rate_limit(self.whatsapp_account)
			super().send_outgoing()
			return

		if provider == "meta":
			self._check_meta_rate_limit(self.whatsapp_account)
			super().send_outgoing()
			return

		# ── OpenWA path ──────────────────────────────────────────────────
		if self._should_queue(session_name):
			self._enqueue_for_later(session_name)
			return

		# Claim the cap slot before sending. Checking first and incrementing
		# after let two concurrent senders both observe room and both send.
		if session_name and not reserve_slot(session_name):
			self._handle_cap_reached(session_name)
			return

		if self.message_type == "Template" or self.template:
			self._send_template_via_openwa(session_name, session_strategy)
		else:
			self._send_text_via_openwa(session_name)

	# ── Meta fallback on the real doc ───────────────────────────────────

	def _meta_send_result(self) -> "SendResult":  # noqa: F821
		"""Execute upstream Meta send on THIS document (no throwaway doc).

		Called as the meta_fallback_fn from within the doctype override context
		so that the REAL document record is the one used for the Meta API call
		and is properly persisted. Returns a SendResult reflecting the outcome.
		"""
		from frappe_whatsapp_openwa.providers.base import SendResult
		try:
			self._check_meta_rate_limit(self.whatsapp_account)
			super().send_outgoing()
			return SendResult(
				success=self.status == "Success",
				provider="meta",
				message_id=self.message_id or "",
				raw_response={},
			)
		except Exception as e:
			return SendResult(
				success=False, provider="meta", message_id=None, raw_response={}, error=str(e),
			)

	# ── OpenWA text path ────────────────────────────────────────────────

	def _send_text_via_openwa(self, session_name: str | None) -> None:
		from frappe_whatsapp_openwa.routing.router import route_send_text

		result = route_send_text(
			account_name=self.whatsapp_account,
			to=self.to,
			body=self.message or "",
			meta_fallback_fn=self._meta_send_result,
			session_override=session_name,
		)
		if result.success:
			self.status = "Success"
			self.message_id = result.message_id or ""
			self.custom_provider_used = result.provider
			# The slot was claimed up front; it only stays claimed if the
			# message actually went out over OpenWA (a Meta fallback consumes
			# no OpenWA capacity).
			if result.provider != "openwa" and session_name:
				release_slot(session_name)
		else:
			if session_name:
				release_slot(session_name)
			self.status = "Failed"
			frappe.throw(
				frappe._(f"OpenWA send failed: {result.error}"),
				title=frappe._("Message Send Failed"),
			)

	# ── OpenWA template path ─────────────────────────────────────────────

	def _send_template_via_openwa(self, session_name: str | None, session_strategy: str | None = None) -> None:
		from frappe_whatsapp_openwa.routing.router import route_send_text
		from frappe_whatsapp_openwa.translators.template_flattener import flatten_template

		template = frappe.get_doc("WhatsApp Templates", self.template)
		params = self._resolve_template_params(template)
		button_labels = _extract_button_labels(template)

		text = flatten_template(
			body=template.template or "",
			parameters=params,
			header=template.header or None,
			footer=template.footer or None,
			buttons=button_labels or None,
		)

		result = route_send_text(
			account_name=self.whatsapp_account,
			to=self.to,
			body=text,
			meta_fallback_fn=self._meta_send_result,
			session_strategy=session_strategy,
			session_override=session_name,
		)
		if result.success:
			self.status = "Success"
			self.message_id = result.message_id or ""
			self.custom_provider_used = result.provider
			if result.provider != "openwa" and session_name:
				release_slot(session_name)
		else:
			if session_name:
				release_slot(session_name)
			self.status = "Failed"
			frappe.throw(
				frappe._(f"OpenWA template send failed: {result.error}"),
				title=frappe._("Template Send Failed"),
			)

	def _resolve_template_params(self, template) -> list[str]:
		from frappe_whatsapp_openwa.translators.template_flattener import extract_params_from_body_param

		if self.body_param:
			return extract_params_from_body_param(self.body_param)

		if getattr(self.flags, "custom_ref_doc", None):
			field_names = (
				(template.field_names or "").split(",")
				if template.field_names
				else (template.sample_values or "").split(",")
			)
			return [
				str(self.flags.custom_ref_doc.get(fn.strip(), ""))
				for fn in field_names if fn.strip()
			]

		if self.reference_doctype and self.reference_name:
			# Upstream renders the reference document's fields into the outgoing
			# message without checking read access. That is harmless while
			# WhatsApp Message create is System Manager only, but the send
			# endpoints are now usable by company-scoped roles (OWA-03), and a
			# sender who cannot read a document must not be able to have its
			# field values rendered into a message and delivered to a number of
			# their choosing.
			#
			# ignore_permissions is honoured so server-side automation that
			# legitimately runs beyond the session user keeps working.
			if not self.flags.ignore_permissions and not frappe.has_permission(
				self.reference_doctype, "read", doc=self.reference_name
			):
				raise frappe.PermissionError(
					frappe._("Not permitted to read {0} {1}").format(
						self.reference_doctype, self.reference_name
					)
				)

			ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
			field_names = (
				(template.field_names or "").split(",")
				if template.field_names
				else (template.sample_values or "").split(",")
			)
			return [
				ref_doc.get_formatted(fn.strip())
				for fn in field_names if fn.strip()
			]

		return []

	# ── Meta rate limiting ─────────────────────────────────────────────

	def _check_meta_rate_limit(self, account_name: str) -> None:
		"""Abort the call if the Meta API per-account rate window is full."""
		from frappe_whatsapp_openwa.utils.rate_limiter import check_rate_limit, raise_rate_limit_error

		allowed, context = check_rate_limit(account_name, action="meta_send")
		if not allowed:
			raise_rate_limit_error(account_name, context)

	# ── Daily soft cap ──────────────────────────────────────────────────

	def _handle_cap_reached(self, session_name: str) -> None:
		try:
			ext = frappe.get_doc("WhatsApp Account Provider Extension", self.whatsapp_account)
			if ext.queue_on_unhealthy:
				self._enqueue_for_later(session_name)
				frappe.msgprint(
					frappe._(f"Daily cap reached for session {session_name} — message queued."),
					indicator="orange",
					alert=True,
				)
				return
		except Exception:
			pass

		frappe.logger().warning(
			f"Daily soft cap reached for OpenWA session {session_name} — routing to Meta."
		)
		self.custom_provider_used = "meta"
		self._check_meta_rate_limit(self.whatsapp_account)
		super().send_outgoing()

	# ── Queue helpers ────────────────────────────────────────────────────

	def _should_queue(self, session_name: str | None) -> bool:
		try:
			ext = frappe.get_doc("WhatsApp Account Provider Extension", self.whatsapp_account)
			if not ext.queue_on_unhealthy:
				return False
		except Exception:
			return False

		from frappe_whatsapp_openwa.utils.cache import is_session_alive
		return session_name is not None and not is_session_alive(session_name)

	def _enqueue_for_later(self, session_name: str | None) -> None:
		from frappe_whatsapp_openwa.queue.enqueue import enqueue_message

		if self.message_type == "Template" or self.template:
			payload = {"template": self.template, "body_param": self.body_param or ""}
			msg_type = "template"
		else:
			payload = {"body": self.message or "", "attach": self.attach or ""}
			msg_type = self.content_type or "text"

		# The queue row is not linked back to this message, so the campaign it
		# belongs to has to travel in the payload — otherwise a message queued
		# because its session was unhealthy would drain on whatever session the
		# account allows, including one the campaign excluded.
		campaign = getattr(self, "bulk_message_reference", None)
		if campaign:
			payload["campaign"] = campaign

		queue_name = enqueue_message(
			account=self.whatsapp_account,
			recipient=self.to,
			message_type=msg_type,
			payload=payload,
			requested_provider="openwa",
		)
		self.status = "Queued"
		frappe.msgprint(
			frappe._(f"Session unhealthy — message queued as {queue_name}"),
			indicator="orange",
			alert=True,
		)




def _extract_button_labels(template) -> list[str]:
	"""Pull button labels from the template's WhatsApp Button child table."""
	labels = []
	for row in getattr(template, "buttons", []) or []:
		label = getattr(row, "button_label", None)
		if label:
			labels.append(str(label))
	return labels
