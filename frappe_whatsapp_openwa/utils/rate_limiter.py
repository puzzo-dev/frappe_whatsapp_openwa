"""Sliding-window Redis rate limiter for outbound Meta API calls.

Meta's Cloud API enforces rate limits per WhatsApp Business Account (WABA).
This module wraps the outbound Meta path in a configurable per-account window.
"""

from __future__ import annotations

import frappe


_DEFAULT_WINDOW_SECONDS = 60
_DEFAULT_MAX_CALLS = 100  # conservative default per minute per account


def check_rate_limit(account_name: str, action: str = "meta_send") -> tuple[bool, dict]:
	"""Return (allowed, context).

	Uses a Redis sliding window: one entry per call, with the key expiring
	after the window. The number of entries in the window is the call count.
	"""
	settings = frappe.get_single("OpenWA Gateway Settings")
	window = settings.get("meta_rate_limit_window_seconds") or _DEFAULT_WINDOW_SECONDS
	max_calls = settings.get("meta_rate_limit_max_calls") or _DEFAULT_MAX_CALLS

	key = f"openwa:ratelimit:meta:{action}:{account_name}"
	now = frappe.utils.now_datetime().timestamp()
	pipe = frappe.cache().pipeline()
	pipe.zremrangebyscore(key, "-inf", now - window)
	pipe.zrange(key, 0, -1, withscores=True)
	pipe.zadd(key, {str(now): now})
	pipe.expire(key, window)
	_, current, _, _ = pipe.execute()
	count = len(current)

	allowed = count < max_calls
	context = {
		"allowed": allowed,
		"window_seconds": window,
		"max_calls": max_calls,
		"current_calls": count,
		"reset_at": frappe.utils.format_datetime(
			frappe.utils.get_datetime(frappe.utils.add_to_date(None, seconds=window))
		),
	}
	return allowed, context


def raise_rate_limit_error(account_name: str, context: dict):
	frappe.throw(
		f"Meta API rate limit exceeded for account {account_name}. "
		f"({context['current_calls']}/{context['max_calls']} calls in "
		f"{context['window_seconds']}s).",
		frappe.TooManyRequestsError,
	)
