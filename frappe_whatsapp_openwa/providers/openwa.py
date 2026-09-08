from __future__ import annotations

from typing import Literal

import httpx

from frappe_whatsapp_openwa.providers.base import (
	GATEWAY_STATUS_MAP,
	OpenWAClientError,
	OpenWANetworkError,
	OpenWARateLimited,
	OpenWASessionDown,
	SendResult,
	SessionStatus,
	WhatsAppProvider,
)
from frappe_whatsapp_openwa.utils.phone import to_wa_format
from frappe_whatsapp_openwa.utils.retry import retry_unless_disabled


class OpenWAAdapter(WhatsAppProvider):
	"""REST client for the shared OpenWA gateway service.

	Pure Python — no Frappe imports. Frappe doc reads happen in overrides/send.py
	before this adapter is constructed.
	"""

	def __init__(
		self,
		gateway_url: str,
		api_key: str,
		session_id: str,
		retry_network_errors: bool = True,
		client: httpx.Client | None = None,
	):
		self.base = gateway_url.rstrip("/")
		self.session_id = session_id
		# The queue worker turns this off: it retries the whole message itself,
		# so retrying here as well just multiplies the calls (see retry.py).
		self.retry_network_errors = retry_network_errors

		# A caller that has a pooled client passes it in. One adapter is built
		# per message, and building a client here meant a fresh TCP and TLS
		# handshake for every send plus a connection pool that nothing ever
		# closed — at campaign volume that is thousands of leaked pools and file
		# descriptors, and a handshake on the critical path of each message.
		# routing.router passes the shared client from utils.gateway.
		#
		# The private one remains for direct construction, which is what the
		# tests do: this module stays free of Frappe imports so it can be
		# exercised without a site.
		self._owns_client = client is None
		self._client = client or httpx.Client(
			timeout=httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0),
			headers={
				"X-API-Key": api_key,
				"Authorization": f"Bearer {api_key}",
			},
		)

	def close(self) -> None:
		"""Close the client, but only if this adapter created it."""
		if self._owns_client:
			self._client.close()

	# ── Public API ────────────────────────────────────────────────────────

	@retry_unless_disabled
	def send_text(self, to: str, body: str, account: str) -> SendResult:
		url = f"{self.base}/api/sessions/{self.session_id}/messages/send-text"
		resp = self._client.post(url, json={"chatId": to_wa_format(to), "text": body})
		self._raise_for_status(resp)
		data = resp.json()
		return SendResult(
			success=True,
			provider="openwa",
			message_id=data.get("messageId") or data.get("id"),
			raw_response=data,
		)

	@retry_unless_disabled
	def send_media(
		self,
		to: str,
		media_url: str,
		caption: str | None,
		media_type: Literal["image", "document", "video", "audio"],
		account: str,
	) -> SendResult:
		endpoint_map = {
			"image": "send-image",
			"document": "send-document",
			"video": "send-video",
			"audio": "send-audio",
		}
		url = (
			f"{self.base}/api/sessions/{self.session_id}/messages/"
			f"{endpoint_map.get(media_type, 'send-document')}"
		)
		payload: dict = {"chatId": to_wa_format(to), "url": media_url}
		if caption:
			payload["caption"] = caption
		resp = self._client.post(url, json=payload)
		self._raise_for_status(resp)
		data = resp.json()
		return SendResult(
			success=True,
			provider="openwa",
			message_id=data.get("messageId") or data.get("id"),
			raw_response=data,
		)

	@retry_unless_disabled
	def send_template(self, to: str, template_name: str, components: dict, account: str) -> SendResult:
		"""OpenWA has no native template concept — caller must pre-flatten via template_flattener."""
		raise NotImplementedError(
			"Use overrides.template.send_template_message which flattens to send_text first."
		)

	@retry_unless_disabled
	def get_session_status(self, session_id: str) -> SessionStatus:
		url = f"{self.base}/api/sessions/{session_id}"
		resp = self._client.get(url)
		self._raise_for_status(resp)
		data = resp.json()
		return SessionStatus(
			status=GATEWAY_STATUS_MAP.get((data.get("status") or "").lower(), "Failed"),
			qr_code=None,
			details=data,
		)

	# ── Internals ─────────────────────────────────────────────────────────

	def _raise_for_status(self, resp: httpx.Response) -> None:
		if resp.status_code == 429:
			raise OpenWARateLimited(f"OpenWA rate-limited: {resp.text}")
		if resp.status_code in (503, 502):
			raise OpenWASessionDown(f"OpenWA gateway down: {resp.status_code}")
		if resp.status_code >= 500:
			raise OpenWASessionDown(f"OpenWA server error {resp.status_code}: {resp.text}")
		if resp.status_code >= 400:
			raise OpenWAClientError(f"OpenWA client error {resp.status_code}: {resp.text}")
