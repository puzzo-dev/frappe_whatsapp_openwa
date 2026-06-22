"""Tenacity retry decorators for OpenWA HTTP calls."""

from __future__ import annotations

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
