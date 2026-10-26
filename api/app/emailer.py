"""Sends email. Without SMTP settings (development) it prints to the server log instead."""
import logging
import smtplib
from email.message import EmailMessage

from . import config

log = logging.getLogger("travelsync.email")
OUTBOX: list[dict] = []  # last messages, used by tests and handy while developing


def send_email(to: str, subject: str, body: str) -> None:
    OUTBOX.append({"to": to, "subject": subject, "body": body})
    del OUTBOX[:-50]
    if not config.SMTP_HOST:
        log.warning("EMAIL (dev mode, not sent) to=%s | %s | %s", to, subject, body)
        print(f"\n--- EMAIL to {to} ---\n{subject}\n{body}\n---------------------\n")
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.MAIL_FROM, to, subject
    msg.set_content(body)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as s:
        s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(msg)
