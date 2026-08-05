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
from frappe_whatsapp_openwa.utils.retry import retry_on_network_error


class OpenWAAdapter(WhatsAppProvider):
	"""REST client for the shared OpenWA gateway service.

	Pure Python — no Frappe imports. Frappe doc reads happen in overrides/send.py
	before this adapter is constructed.
	"""

	def __init__(self, gateway_url: str, api_key: str, session_id: str):
		self.base = gateway_url.rstrip("/")
		self.session_id = session_id
		self._client = httpx.Client(
			timeout=httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0),
			headers={
				"X-API-Key": api_key,
				"Authorization": f"Bearer {api_key}",
			},
		)

	# ── Public API ────────────────────────────────────────────────────────

	@retry_on_network_error
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

	@retry_on_network_error
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

	@retry_on_network_error
	def send_template(self, to: str, template_name: str, components: dict, account: str) -> SendResult:
		"""OpenWA has no native template concept — caller must pre-flatten via template_flattener."""
		raise NotImplementedError(
			"Use overrides.template.send_template_message which flattens to send_text first."
		)

	@retry_on_network_error
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
