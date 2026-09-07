"""Tenacity retry decorators for OpenWA HTTP calls."""

from __future__ import annotations

import functools

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# Two attempts, exponential backoff capped at 2s.
# Anything beyond 2 attempts is the fallback chain's job.
retry_on_network_error = retry(
	retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
	stop=stop_after_attempt(2),
	wait=wait_exponential(multiplier=0.5, max=2),
	reraise=True,
)


def retry_unless_disabled(fn):
	"""Apply the network retry, unless the adapter instance has turned it off.

	Retrying belongs to exactly one layer. For a synchronous desk or API send
	that is the adapter, because nothing else will try again. For a queued
	message it is the worker, which already owns six attempts with backoff — and
	stacking the two multiplied a single network blip into up to a dozen gateway
	calls. A timeout is not proof of non-delivery, so each of those can put the
	same message in front of the customer again, and the gateway offers no
	idempotency key to make that safe.
	"""
	with_retry = retry_on_network_error(fn)

	@functools.wraps(fn)
	def inner(self, *args, **kwargs):
		if getattr(self, "retry_network_errors", True):
			return with_retry(self, *args, **kwargs)
		return fn(self, *args, **kwargs)

	return inner
