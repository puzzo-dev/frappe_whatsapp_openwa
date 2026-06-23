"""Operational metrics for the WhatsApp Dual Gateway.

Exposes a whitelisted API so dashboards / external monitors can query the
health of sessions, queues, and message throughput without needing direct
DB access.
"""

from __future__ import annotations

import frappe


@frappe.whitelist()
def get_gateway_metrics() -> dict:
	"""Return current operational metrics for the WhatsApp Dual Gateway."""
	return {
		"sessions": _session_metrics(),
		"queue": _queue_metrics(),
		"messages": _message_metrics(),
	}


def _session_metrics() -> dict:
	statuses = frappe.get_all(
		"OpenWA Session",
		fields=["status", "count(*) as count"],
		group_by="status",
	)
	status_counts = {row.status: row.count for row in statuses}
	return {
		"total": sum(status_counts.values()),
		"by_status": status_counts,
		"healthy": status_counts.get("Connected", 0),
		"disconnected": status_counts.get("Disconnected", 0),
		"qr_required": status_counts.get("QR Required", 0),
	}


def _queue_metrics() -> dict:
	statuses = frappe.get_all(
		"WhatsApp Outbound Queue",
		fields=["status", "count(*) as count"],
		group_by="status",
	)
	status_counts = {row.status: row.count for row in statuses}
	return {
		"total": sum(status_counts.values()),
		"by_status": status_counts,
		"stuck": status_counts.get("Sending", 0),
		"failed": status_counts.get("Failed", 0),
	}


def _message_metrics() -> dict:
	"""Aggregate message counts over the last 24 hours by provider."""
	cutoff = frappe.utils.add_to_date(frappe.utils.now(), hours=-24)
	rows = frappe.get_all(
		"WhatsApp Message",
		filters={"creation": [">=", cutoff]},
		fields=["custom_provider_used", "count(*) as count"],
		group_by="custom_provider_used",
	)
	return {
		"last_24h": {row.custom_provider_used or "unknown": row.count for row in rows},
	}
