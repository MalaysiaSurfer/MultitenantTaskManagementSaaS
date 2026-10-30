import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import settings

MAIL_DELIVERY_ERRORS = (smtplib.SMTPException, OSError)


def mail_delivery_enabled() -> bool:
    return settings.smtp_host is not None


def _send(message: EmailMessage) -> None:
    with smtplib.SMTP(
        settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
    ) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username is not None:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(message)


async def send_email(to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    await asyncio.to_thread(_send, message)
