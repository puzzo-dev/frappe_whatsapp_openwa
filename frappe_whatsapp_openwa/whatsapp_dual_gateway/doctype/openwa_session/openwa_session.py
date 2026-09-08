import frappe
from frappe.model.document import Document


# Fields that are gateway-driven only — never user-editable.
# Sync paths set frappe.flags.openwa_sync before saving.
_GATEWAY_DRIVEN_FIELDS = ("status", "gateway_session_id")


class OpenWASession(Document):
	def validate(self):
		"""Reject manual edits to gateway-driven fields.

		Sync paths (provision, health poll, webhooks, self-healer, session API)
		set ``frappe.flags.openwa_sync = True`` before writing. Without that
		flag, any change to ``status`` or ``gateway_session_id`` is rejected —
		closing the REST-API and form manual-edit hole.
		"""
		# One WhatsApp number, one session.
		#
		# Normalised before the uniqueness check, because the constraint is on
		# the stored string: +2348012345678, 08012345678 and 2348012345678 are
		# the same number and would otherwise all be accepted as separate
		# sessions, each trying to drive the same WhatsApp account.
		self._normalise_phone_number()

		if frappe.flags.get("openwa_sync"):
			return

		if self.is_new():
			return  # Creation is allowed; defaults apply.

		doc_before = self.get_doc_before_save()
		if not doc_before:
			return

		for fieldname in _GATEWAY_DRIVEN_FIELDS:
			old_val = doc_before.get(fieldname)
			new_val = self.get(fieldname)
			if old_val != new_val:
				frappe.throw(
					frappe._(
						"{0} is gateway-driven and cannot be edited manually. "
						"Use the gateway sync actions (provision, restart, or wait "
						"for the next health check)."
					).format(frappe.bold(frappe._(fieldname.replace("_", " ").title()))),
					title=frappe._("Field Locked"),
				)

	def before_save(self):
		self.requires_human_attention = 1 if self.status in (
			"QR Required",
			"Failed",
		) else 0

	def on_update(self):
		self._enforce_single_default_per_account()

	def _enforce_single_default_per_account(self):
		"""Only one session per WhatsApp Account may be flagged is_default.

		Setting it here atomically clears it on every sibling session so the
		resolver's default-session lookup is deterministic.
		"""
		if not (self.is_default and self.linked_whatsapp_account):
			return
		frappe.db.sql(
			"""UPDATE `tabOpenWA Session`
			   SET is_default = 0
			   WHERE linked_whatsapp_account = %s
			   AND is_default = 1
			   AND name != %s""",
			(self.linked_whatsapp_account, self.name),
		)

	def _normalise_phone_number(self) -> None:
		"""Store the number in one canonical form so uniqueness means something."""
		if not self.phone_number:
			return

		from frappe_whatsapp_openwa.utils.phone import normalise_e164

		normalised = normalise_e164(self.phone_number)
		if normalised and normalised != self.phone_number:
			self.phone_number = normalised

		# The unique index reports the collision, but not usefully: name the
		# session already using this number so the operator can go to it.
		existing = frappe.db.get_value(
			"OpenWA Session",
			{"phone_number": self.phone_number, "name": ("!=", self.name or "")},
			["name", "session_name"],
			as_dict=True,
		)
		if existing:
			frappe.throw(
				frappe._("{0} already uses {1}. One phone number drives one session — "
				         "reconnect that session instead of creating another.").format(
					existing.session_name or existing.name, self.phone_number
				),
				title=frappe._("Phone Number Already In Use"),
			)

	def on_trash(self):
		"""Release the gateway session this document owns.

		The gateway session outlives the document unless it is told otherwise,
		and this document holds the only reference to it. Deleting without this
		strands a live session on the gateway that nothing can reconnect or
		reuse — they simply accumulate.

		The id is captured before deletion because the row is about to go.
		"""
		gateway_session_id = self.gateway_session_id
		if not gateway_session_id:
			return

		from frappe_whatsapp_openwa.api.provision import release_gateway_session

		release_gateway_session(self.name, gateway_session_id)

	def after_insert(self):
		"""Provision on the gateway in the background.

		The form reloads via the openwa_session_state_change realtime event
		once the gateway has created and started the session (QR code shows
		up as soon as the engine emits it). If the enqueue itself fails the
		operator can still use the "Create Session on Gateway" button.

		Skipped when ``frappe.flags.openwa_skip_provision`` is set (tests).
		"""
		if frappe.flags.get("openwa_skip_provision"):
			return

		from frappe.utils.background_jobs import is_job_enqueued

		job_id = f"openwa:provision:{self.name}"
		try:
			if is_job_enqueued(job_id):
				return
			frappe.enqueue(
				"frappe_whatsapp_openwa.api.provision.provision_session_async",
				session_name=self.name,
				queue="short",
				timeout=60,
				job_id=job_id,
				# Without this the worker can start before this document's
				# transaction commits and fail with DoesNotExistError on a
				# session that is about to exist.
				enqueue_after_commit=True,
			)
		except Exception:
			frappe.log_error(
				title=f"OpenWA auto-provision enqueue failed for {self.name}",
				message=frappe.get_traceback(),
			)

	@frappe.whitelist()
	def restart_session(self):
		"""Trigger a manual restart of this session via the gateway.

		frappe.handler.run_doc_method only checks has_permission("read") before
		invoking a whitelisted document method, so without this a read-only user
		could drop a live WhatsApp connection. Restarting is a write to the
		session's operational state and is gated as one.
		"""
		self.check_permission("write")

		if not self.gateway_session_id:
			frappe.throw(
				frappe._("This session is not registered on the gateway yet."),
				title=frappe._("Not Provisioned"),
			)

		from frappe_whatsapp_openwa.monitoring.self_healer import _restart_on_gateway
		from frappe_whatsapp_openwa.utils.gateway import get_gateway_client

		client = get_gateway_client(timeout=35.0)
		if not _restart_on_gateway(client, self.gateway_session_id):
			frappe.throw(
				frappe._("The gateway rejected the restart. Check the Last Error field and the gateway logs."),
				title=frappe._("Restart Failed"),
			)

		self.restart_attempt_count = (self.restart_attempt_count or 0) + 1
		self.last_state_change = frappe.utils.now()
		# Sync path — bypass the manual-edit guard.
		frappe.flags.openwa_sync = True
		try:
			self.save(ignore_permissions=True)
		finally:
			frappe.flags.openwa_sync = False
		return {"status": "restart_issued"}


def on_doctype_update():
	"""Indexes for the columns every send and every inbound event filters on.

	Declared here as the source of truth; patches/index_openwa_session covers
	sites where the DocType is not reloaded during migrate, which is when this
	hook does not fire.
	"""
	# gateway_session_id and status carry search_index on the field, so Frappe
	# already indexes them; repeating them here would only duplicate.
	frappe.db.add_index("OpenWA Session", ["linked_whatsapp_account"])
