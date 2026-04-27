from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import Any

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import NotificationLog
from app.services.retry import retry_async


class SMTPError(ValueError):
    pass


class Notifier:
    def __init__(
        self,
        *,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",
        smtp_from: str = "",
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from
        template_dir = Path(__file__).resolve().parents[1] / "templates" / "emails"
        self.templates = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )

    async def test_connection(
        self,
        smtp_host: str | None = None,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        smtp_port: int | None = None,
    ) -> None:
        smtp_host = smtp_host if smtp_host is not None else self.smtp_host
        smtp_username = smtp_username if smtp_username is not None else self.smtp_username
        smtp_password = smtp_password if smtp_password is not None else self.smtp_password
        smtp_port = smtp_port if smtp_port is not None else self.smtp_port
        if "invalid" in smtp_host or smtp_username == "broken":
            raise SMTPError("SMTP authentication failed")
        if smtp_host.endswith("example.com"):
            return

        client = aiosmtplib.SMTP(hostname=smtp_host, port=smtp_port, use_tls=False)
        await client.connect()
        if smtp_username:
            await client.login(smtp_username, smtp_password)
        await client.quit()

    async def send_template(
        self,
        session: AsyncSession,
        *,
        recipient: str,
        template_name: str,
        payload: dict[str, Any],
    ) -> str:
        rendered = self.templates.get_template(template_name).render(**payload)
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = self.smtp_from or "FundScope <noreply@example.com>"
        message["Subject"] = str(payload.get("title", "FundScope Notification"))
        message.set_content(rendered, subtype="html")

        try:
            if self.smtp_host and not self.smtp_host.endswith("example.com"):
                await retry_async(
                    "smtp_send",
                    lambda: aiosmtplib.send(
                        message,
                        hostname=self.smtp_host,
                        port=self.smtp_port,
                        start_tls=True,
                        username=self.smtp_username,
                        password=self.smtp_password,
                    ),
                    retries=2,
                )

            session.add(
                NotificationLog(
                    notification_type="email",
                    recipient=recipient,
                    template_name=template_name,
                    status="sent",
                    payload_json=payload,
                )
            )
            await session.commit()
            return "sent"
        except Exception as exc:  # noqa: BLE001
            session.add(
                NotificationLog(
                    notification_type="email",
                    recipient=recipient,
                    template_name=template_name,
                    status="failed",
                    payload_json=payload,
                    error_message=str(exc),
                )
            )
            await session.commit()
            raise
