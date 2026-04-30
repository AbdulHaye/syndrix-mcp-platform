from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import structlog

from app.adapters.base import BaseAdapter
from app.config import get_settings

logger = structlog.get_logger(__name__)


class EmailAdapter(BaseAdapter):
    """SMTP email adapter using aiosmtplib."""

    def __init__(self) -> None:
        settings = get_settings()
        self._host = settings.smtp_host
        self._port = settings.smtp_port or 587
        self._user = settings.smtp_user
        self._password = settings.smtp_password
        self._from_address = settings.smtp_from or settings.smtp_user
        self._connected: bool = False

    async def connect(self) -> None:
        if not self._user or not self._password or not self._host:
            logger.warning("email_credentials_missing")
            self._connected = False
            return
        try:
            import aiosmtplib

            smtp = aiosmtplib.SMTP(hostname=self._host, port=self._port, use_tls=False)
            await smtp.connect()
            await smtp.starttls()
            await smtp.login(self._user, self._password)
            await smtp.quit()
            self._connected = True
            logger.info("email_connected", host=self._host, port=self._port)
        except Exception as exc:
            logger.error("email_connect_failed", error=str(exc))
            self._connected = False
            raise

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("email_disconnected")

    def is_connected(self) -> bool:
        return self._connected

    async def _ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        return {"status": "not_applicable", "adapter": "email", "note": "Email has no contact lookup."}

    async def create_note(self, client_id: str, note: str) -> dict[str, Any]:
        return {"status": "not_applicable", "adapter": "email", "note": "Email has no note concept."}

    async def search_leads(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"status": "not_applicable", "adapter": "email"}]

    async def send_email(self, to: str, subject: str, body: str) -> dict[str, Any]:
        await self._ensure_connected()
        import aiosmtplib

        message = MIMEMultipart("alternative")
        message["From"] = self._from_address or self._user or ""
        message["To"] = to
        message["Subject"] = subject
        message.attach(MIMEText(body, "plain", "utf-8"))

        try:
            await aiosmtplib.send(
                message,
                hostname=self._host,
                port=self._port,
                username=self._user,
                password=self._password,
                use_tls=False,
                start_tls=True,
            )
            logger.info("email_sent", to=to, subject=subject)
            return {"success": True, "to": to, "subject": subject}
        except Exception as exc:
            logger.error("email_send_failed", error=str(exc), to=to)
            raise

    async def draft_email(self, to: str, subject: str, body: str) -> dict[str, Any]:
        return {
            "success": True,
            "draft": {
                "from": self._from_address or self._user,
                "to": to,
                "subject": subject,
                "body": body,
            },
            "note": "Draft created in memory — call send_email to deliver.",
        }


# Singleton
email_adapter = EmailAdapter()
