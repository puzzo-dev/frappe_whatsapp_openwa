"""Redis-based cron lock to prevent overlapping scheduler runs."""

from __future__ import annotations

import frappe


def acquire_cron_lock(lock_name: str, ttl_seconds: int = 55) -> bool:
	"""Try to acquire an exclusive cron lock. Returns True if acquired.

	Uses Redis SET NX (set if not exists) with an expiry so the lock
	auto-releases even if the worker crashes before finishing.

	ttl_seconds should be slightly less than the cron interval so the lock
	expires before the next tick fires. Default 55 s suits * * * * * jobs.
	"""
	# make_key prefixes the site's db_name so locks stay tenant-scoped;
	# redis-py SET with nx=True and ex=ttl is atomic (single SETNX + EXPIRE).
	key = frappe.cache.make_key(f"openwa:cronlock:{lock_name}")
	acquired = frappe.cache.set(key, "1", ex=ttl_seconds, nx=True)
	return bool(acquired)


def release_cron_lock(lock_name: str) -> None:
	"""Release a cron lock before its TTL expires (optional — lock auto-expires)."""
	frappe.cache.delete_value(f"openwa:cronlock:{lock_name}")
