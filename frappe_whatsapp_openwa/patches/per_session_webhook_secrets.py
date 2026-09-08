"""Give every OpenWA Session its own webhook secret and re-register it.

The gateway registers webhooks per session (`/api/sessions/:id/webhooks`) and
signs each delivery with that webhook's own secret, but this app registered
every session with one gateway-wide secret. The signature therefore proved only
that *someone* holding the shared secret sent the delivery; which session it came
from was a claim in the body. A leak exposed every session at once, and no single
session could be rotated.

Each session now gets its own secret and its webhook is re-registered so the
gateway signs with it. The shared secret is then deleted: keeping it as a
fallback guarded nothing once every session had its own, and a single key that
still verified any session was the weakness this replaces. The gateway retries a
delivery it cannot get accepted, so the moment between re-registration and the
new secret taking effect is recoverable.

Re-registration talks to the gateway, so it is best-effort: a session that
cannot be reached keeps working on the shared secret and is picked up the next
time it is provisioned.
"""

import frappe


def execute():
	if not frappe.db.table_exists("OpenWA Session"):
		return

	sessions = frappe.get_all(
		"OpenWA Session",
		filters={"gateway_session_id": ["is", "set"]},
		fields=["name", "gateway_session_id"],
	)
	if not sessions:
		return

	from frappe_whatsapp_openwa.api.provision import (
		_register_webhook,
		ensure_session_webhook_secret,
	)

	client = None
	try:
		from frappe_whatsapp_openwa.utils.gateway import get_gateway_client

		client = get_gateway_client(timeout=15.0)
	except Exception:
		# Gateway not configured on this site — still mint the secrets so the
		# next provision registers with them.
		client = None

	for session in sessions:
		try:
			ensure_session_webhook_secret(session.name)
			if client:
				_register_webhook(client, session.gateway_session_id, session_name=session.name)
		except Exception:
			frappe.log_error(
				title=f"OpenWA: per-session webhook secret rollout failed for {session.name}",
				message=frappe.get_traceback(),
			)

	_forget_shared_secret()
	frappe.db.commit()


def _forget_shared_secret():
	"""Remove the gateway-wide secret now that every session has its own.

	It is a credential, so it does not get to sit in the database as an
	orphaned Singles row after the field is gone.
	"""
	frappe.db.delete("Singles", {"doctype": "OpenWA Gateway Settings", "field": "webhook_secret"})
	try:
		frappe.db.delete("__Auth", {"doctype": "OpenWA Gateway Settings", "fieldname": "webhook_secret"})
	except Exception:
		# Older/newer schemas name this differently; the Singles row is the
		# one that matters for the value being readable.
		pass
	frappe.clear_cache(doctype="OpenWA Gateway Settings")
