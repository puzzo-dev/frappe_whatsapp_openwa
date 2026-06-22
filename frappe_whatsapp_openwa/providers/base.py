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


class OpenWASessionDown(Exception):
	pass


class OpenWARateLimited(Exception):
	pass


class OpenWANetworkError(Exception):
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
