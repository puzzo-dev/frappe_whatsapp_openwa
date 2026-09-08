"""Carry per-number limits from Gateway Settings onto each session.

These limits govern one WhatsApp number: how fast it may send, how many restart
attempts it gets, how often it may ask for a pairing code, how many inbound
events it accepts. A single gateway-wide figure had to be set for the most
fragile number and then throttled every other one, so they now live on the
session where they are actually needed.

Anything an operator had deliberately changed is carried across to every
session, so behaviour after the upgrade matches behaviour before it. Values
still at the shipped default are left alone — the session fields carry the same
defaults, and copying them would turn "not configured" into "configured", which
is the difference between inheriting a future default and being pinned to
today's.
"""

import frappe

# gateway fieldname -> (session fieldname, shipped default)
_MOVED = {
	"send_rate_max": ("send_rate_max", 20),
	"send_rate_window_seconds": ("send_rate_window_seconds", 60),
	"max_restart_attempts": ("max_restart_attempts", 3),
	"pairing_code_max_requests": ("pairing_code_max_requests", 5),
	"pairing_code_window_seconds": ("pairing_code_window_seconds", 600),
	"webhook_rate_limit_max": ("webhook_rate_limit_max", 200),
	"webhook_rate_limit_window_seconds": ("webhook_rate_limit_window_seconds", 60),
}


def execute():
	if not frappe.db.table_exists("OpenWA Session"):
		return

	sessions = frappe.get_all("OpenWA Session", pluck="name")
	if not sessions:
		_forget_old_values()
		return

	for gateway_field, (session_field, shipped_default) in _MOVED.items():
		stored = frappe.db.get_value(
			"Singles", {"doctype": "OpenWA Gateway Settings", "field": gateway_field}, "value"
		)
		try:
			stored = int(stored or 0)
		except (TypeError, ValueError):
			continue

		# Untouched, or already the shipped value: leave the sessions inheriting.
		if not stored or stored == shipped_default:
			continue

		for name in sessions:
			frappe.db.set_value(
				"OpenWA Session", name, session_field, stored, update_modified=False
			)

	_forget_old_values()
	frappe.db.commit()


def _forget_old_values():
	"""Drop the orphaned Singles rows the removed fields leave behind."""
	for gateway_field in _MOVED:
		frappe.db.delete("Singles", {"doctype": "OpenWA Gateway Settings", "field": gateway_field})
	frappe.clear_cache(doctype="OpenWA Gateway Settings")
