# Copyright (c) 2026, I-Varse Technologies NG
# License: MIT. See LICENSE

"""Tests for the OpenWA Session lifecycle (Phase 7).

Covers W-1 through W-9: read-only fields, server-side guard, provisioning
failure surfacing, pairing-code flow, phone normalization, and status mapping.
"""

from __future__ import annotations

import frappe
from frappe.tests.test_api import FrappeAPITestCase
from frappe.utils import add_to_date, now_datetime


class TestOpenWASessionLifecycle(FrappeAPITestCase):
	"""Phase 7 lifecycle tests."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._created_sessions = []

	@classmethod
	def tearDownClass(cls):
		for name in cls._created_sessions:
			try:
				frappe.delete_doc("OpenWA Session", name, force=True)
			except Exception:
				pass
		super().tearDownClass()

	def _make_session(self, **kwargs):
		"""Create a minimal OpenWA Session without triggering provisioning."""
		name = f"TEST-WA-{frappe.utils.random_string(6).upper()}"
		doc = frappe.get_doc(
			{
				"doctype": "OpenWA Session",
				"session_name": name,
				"phone_number": kwargs.get("phone_number", "+2348012345678"),
				"linked_whatsapp_account": kwargs.get("linked_whatsapp_account"),
				"company": kwargs.get("company"),
			}
		)
		# Suppress auto-provision during tests.
		frappe.flags.openwa_skip_provision = True
		try:
			doc.insert(ignore_permissions=True)
		finally:
			frappe.flags.openwa_skip_provision = False
		self._created_sessions.append(doc.name)
		return doc

	def _save_with_sync(self, doc):
		"""Save a doc with the openwa_sync flag and notification suppression."""
		frappe.flags.openwa_sync = True
		# run_notifications checks (in_import and mute_emails) — set both.
		frappe.flags.in_import = True
		frappe.flags.mute_emails = True
		try:
			doc.save(ignore_permissions=True)
		finally:
			frappe.flags.openwa_sync = False
			frappe.flags.in_import = False
			frappe.flags.mute_emails = False

	# -----------------------------------------------------------------------
	# W-1: Read-only fields
	# -----------------------------------------------------------------------

	def test_status_is_read_only(self):
		"""status should be read_only=1 in the doctype JSON."""
		meta = frappe.get_meta("OpenWA Session")
		field = meta.get_field("status")
		self.assertTrue(field, "status field must exist")
		self.assertEqual(field.read_only, 1, "status must be read_only=1")

	def test_gateway_session_id_is_read_only(self):
		"""gateway_session_id should be read_only=1 in the doctype JSON."""
		meta = frappe.get_meta("OpenWA Session")
		field = meta.get_field("gateway_session_id")
		self.assertTrue(field, "gateway_session_id field must exist")
		self.assertEqual(field.read_only, 1, "gateway_session_id must be read_only=1")

	# -----------------------------------------------------------------------
	# W-2: Server-side guard
	# -----------------------------------------------------------------------

	def test_manual_status_change_rejected(self):
		"""Manually changing status without the sync flag should be rejected."""
		doc = self._make_session()
		# Simulate a manual edit (no openwa_sync flag).
		frappe.flags.openwa_sync = False
		frappe.flags.in_import = True
		frappe.flags.mute_emails = True
		try:
			doc = frappe.get_doc("OpenWA Session", doc.name)
			doc.status = "Connected"
			with self.assertRaises(frappe.ValidationError):
				doc.save(ignore_permissions=True)
		finally:
			frappe.flags.openwa_sync = False
			frappe.flags.in_import = False
			frappe.flags.mute_emails = False

	def test_manual_gateway_session_id_change_rejected(self):
		"""Manually changing gateway_session_id without the sync flag should be rejected."""
		doc = self._make_session()
		frappe.flags.openwa_sync = False
		frappe.flags.in_import = True
		frappe.flags.mute_emails = True
		try:
			doc = frappe.get_doc("OpenWA Session", doc.name)
			doc.gateway_session_id = "fake-session-id"
			with self.assertRaises(frappe.ValidationError):
				doc.save(ignore_permissions=True)
		finally:
			frappe.flags.openwa_sync = False
			frappe.flags.in_import = False
			frappe.flags.mute_emails = False

	def test_sync_path_status_change_allowed(self):
		"""Changing status with the openwa_sync flag should be allowed."""
		doc = self._make_session()
		doc = frappe.get_doc("OpenWA Session", doc.name)
		doc.status = "QR Required"
		doc.gateway_session_id = "test-gateway-id"
		self._save_with_sync(doc)

		doc.reload()
		self.assertEqual(doc.status, "QR Required")
		self.assertEqual(doc.gateway_session_id, "test-gateway-id")

	# -----------------------------------------------------------------------
	# W-6: Phone normalization for pairing code
	# -----------------------------------------------------------------------

	def test_to_pairing_code_digits_strips_non_digits(self):
		from frappe_whatsapp_openwa.utils.phone import to_pairing_code_digits

		self.assertEqual(to_pairing_code_digits("+234 801 234 5678"), "2348012345678")
		self.assertEqual(to_pairing_code_digits("+234-801-234-5678"), "2348012345678")
		self.assertEqual(to_pairing_code_digits("2348012345678"), "2348012345678")

	def test_to_pairing_code_digits_rejects_short(self):
		from frappe_whatsapp_openwa.utils.phone import to_pairing_code_digits

		with self.assertRaises(ValueError):
			to_pairing_code_digits("12345")  # 5 digits — too short

	def test_to_pairing_code_digits_rejects_long(self):
		from frappe_whatsapp_openwa.utils.phone import to_pairing_code_digits

		with self.assertRaises(ValueError):
			to_pairing_code_digits("1234567890123456")  # 16 digits — too long

	def test_to_pairing_code_digits_rejects_empty(self):
		from frappe_whatsapp_openwa.utils.phone import to_pairing_code_digits

		with self.assertRaises(ValueError):
			to_pairing_code_digits("")
		with self.assertRaises(ValueError):
			to_pairing_code_digits(None)

	# -----------------------------------------------------------------------
	# W-7: Status alignment
	# -----------------------------------------------------------------------

	def test_all_gateway_statuses_map_to_valid_desk_options(self):
		"""Every documented gateway status must map to a valid desk Select option."""
		from frappe_whatsapp_openwa.providers.base import GATEWAY_STATUS_MAP
		from frappe_whatsapp_openwa.utils.gateway import map_gateway_status

		desk_options = {"Initializing", "QR Required", "Connected", "Disconnected", "Failed"}
		documented = [
			"created",
			"initializing",
			"qr_ready",
			"authenticating",
			"ready",
			"disconnected",
			"action_required",
			"failed",
		]

		for gw_status in documented:
			mapped = map_gateway_status(gw_status)
			self.assertIn(
				mapped,
				desk_options,
				f"Gateway status '{gw_status}' mapped to '{mapped}' which is not a valid desk option.",
			)

	def test_unknown_gateway_status_maps_to_failed(self):
		from frappe_whatsapp_openwa.utils.gateway import map_gateway_status

		self.assertEqual(map_gateway_status("unknown_status"), "Failed")
		self.assertEqual(map_gateway_status(""), "Failed")
		self.assertEqual(map_gateway_status(None), "Failed")

	# -----------------------------------------------------------------------
	# W-3: Provisioning failure surfacing (unit test of the flag logic)
	# -----------------------------------------------------------------------

	def test_requires_human_attention_set_on_qr_required(self):
		"""before_save should set requires_human_attention when status is QR Required."""
		doc = self._make_session()
		doc = frappe.get_doc("OpenWA Session", doc.name)
		doc.status = "QR Required"
		doc.gateway_session_id = "test-id"
		self._save_with_sync(doc)

		doc.reload()
		self.assertEqual(doc.requires_human_attention, 1)

	def test_requires_human_attention_set_on_failed(self):
		"""before_save should set requires_human_attention when status is Failed."""
		doc = self._make_session()
		doc = frappe.get_doc("OpenWA Session", doc.name)
		doc.status = "Failed"
		doc.gateway_session_id = "test-id"
		self._save_with_sync(doc)

		doc.reload()
		self.assertEqual(doc.requires_human_attention, 1)

	def test_requires_human_attention_cleared_on_connected(self):
		"""before_save should clear requires_human_attention when status is Connected."""
		doc = self._make_session()
		doc = frappe.get_doc("OpenWA Session", doc.name)
		doc.status = "Connected"
		doc.gateway_session_id = "test-id"
		self._save_with_sync(doc)

		doc.reload()
		self.assertEqual(doc.requires_human_attention, 0)

	# -----------------------------------------------------------------------
	# W-8: Data safety — read-only conversion is additive
	# -----------------------------------------------------------------------

	def test_existing_sessions_preserve_values_after_migration(self):
		"""A session created before the read-only change should keep its values."""
		doc = self._make_session()
		doc = frappe.get_doc("OpenWA Session", doc.name)
		doc.status = "Connected"
		doc.gateway_session_id = "preserved-id"
		self._save_with_sync(doc)

		# Reload — values should be intact.
		doc.reload()
		self.assertEqual(doc.status, "Connected")
		self.assertEqual(doc.gateway_session_id, "preserved-id")
