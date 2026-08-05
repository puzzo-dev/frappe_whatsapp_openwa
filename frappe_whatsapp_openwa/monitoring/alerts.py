"""Session alert helpers — intentionally a no-op.

The extension app does NOT auto-create or auto-send Frappe Notifications.
Users who want email/SMS alerts for session state changes should create
their own Frappe Notification records linked to the OpenWA Session doctype.

The `requires_human_attention` field on OpenWA Session remains available
for list views, standard filters, and custom notifications that the user
chooses to set up.
"""
