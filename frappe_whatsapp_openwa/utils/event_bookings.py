"""event_bookings integration handler.

Implements the ``event_booking_whatsapp_reminder`` hook fired by the
event_bookings app scheduler.  Registered in hooks.py so this handler
is only active when frappe_whatsapp_openwa is installed — event_bookings
itself never imports from this app.

Delivery chain:
  event_bookings scheduler
    → frappe.get_hooks("event_booking_whatsapp_reminder")
    → send_event_booking_reminder(booking_name, phone)          ← this file
    → WhatsApp Notification.send_template_message(eb_doc, phone)
    → WhatsApp Message insert
    → WhatsAppMessageDualGateway.before_insert() routes to Meta or OpenWA
"""

from __future__ import annotations

import frappe
from frappe import _

from frappe_whatsapp_openwa.utils.phone import normalise_e164


def send_event_booking_reminder(booking_name: str, phone: str) -> None:
	"""Send a pre-event WhatsApp reminder for the given Event Booking.

	Called by the event_bookings scheduler via the
	``event_booking_whatsapp_reminder`` hook.

	Looks up the first enabled ``WhatsApp Notification`` configured for
	``Event Booking`` and delegates to ``send_template_message()``.  The
	resulting ``WhatsApp Message`` document is automatically routed through
	``WhatsAppMessageDualGateway`` — no special handling needed here.

	Args:
	    booking_name: Name (ID) of the Event Booking document.
	    phone:        Raw mobile number resolved by event_bookings from the
	                  Customer's primary Contact (``mobile_no`` field).
	"""
	notification_name = frappe.db.get_value(
		"WhatsApp Notification",
		{"reference_doctype": "Event Booking", "disabled": 0},
		"name",
	)
	if not notification_name:
		frappe.logger("frappe_whatsapp_openwa").debug(
			"event_booking_whatsapp_reminder: no enabled WhatsApp Notification "
			"configured for Event Booking — skipping %s",
			booking_name,
		)
		return

	try:
		normalised_phone = normalise_e164(phone)
	except ValueError:
		frappe.log_error(
			title=_("Invalid phone number for Event Booking WhatsApp reminder"),
			message=_("Booking {0}: could not normalise phone '{1}'").format(booking_name, phone),
		)
		return

	try:
		notification = frappe.get_doc("WhatsApp Notification", notification_name)
		eb_doc = frappe.get_doc("Event Booking", booking_name)
		notification.send_template_message(eb_doc, phone_no=normalised_phone)
	except Exception:
		frappe.log_error(
			title=_("WhatsApp reminder failed for Event Booking {0}").format(booking_name),
		)
