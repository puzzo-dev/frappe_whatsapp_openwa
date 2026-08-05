from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SendResult:
	success: bool
	provider: str
	message_id: str | None
	raw_response: dict
	error: str | None = None


@dataclass
class SessionStatus:
	status: str
	qr_code: str | None = None
	details: dict = field(default_factory=dict)


# Gateway SessionStatus enum (lowercase, from the OpenWA gateway) → desk status
# shown on the OpenWA Session doctype. Unknown values map to "Failed".
GATEWAY_STATUS_MAP = {
	"created": "Initializing",
	"initializing": "Initializing",
	"qr_ready": "QR Required",
	"authenticating": "QR Required",
	"ready": "Connected",
	"disconnected": "Disconnected",
	"action_required": "Failed",
	"failed": "Failed",
}


class OpenWASessionDown(Exception):
	pass


class OpenWARateLimited(Exception):
	pass


class OpenWANetworkError(Exception):
	pass


class OpenWAClientError(Exception):
	"""4xx response from the OpenWA gateway (auth failure, session not found, etc.).

	Not a transient error — falling back to Meta is correct behaviour.
	"""
	pass


class WhatsAppProvider(ABC):
	@abstractmethod
	def send_text(self, to: str, body: str, account: str) -> SendResult: ...

	@abstractmethod
	def send_media(
		self,
		to: str,
		media_url: str,
		caption: str | None,
		media_type: Literal["image", "document", "video", "audio"],
		account: str,
	) -> SendResult: ...

	@abstractmethod
	def send_template(
		self, to: str, template_name: str, components: dict, account: str
	) -> SendResult: ...

	@abstractmethod
	def get_session_status(self, session_id: str) -> SessionStatus: ...
