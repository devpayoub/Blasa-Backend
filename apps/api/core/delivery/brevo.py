"""Brevo (ex-Sendinblue) HTTP delivery.

Render's free tier blocks outbound SMTP ports, so transactional email and SMS
go through Brevo's HTTPS API instead. Both helpers are best-effort: a provider
outage is logged and reported as "not sent" so the calling request or Celery
task never crashes because a third party is down.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger("blasa.delivery.brevo")

EMAIL_ENDPOINT = "https://api.brevo.com/v3/smtp/email"
SMS_ENDPOINT = "https://api.brevo.com/v3/transactionalSMS/sms"
E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


def _post(url: str, payload: dict, *, timeout: int = 10) -> bool:
    api_key = getattr(settings, "BREVO_API_KEY", "")
    if not api_key:
        logger.warning("Brevo is not configured; delivery skipped")
        return False
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        # Never log the payload: it carries OTP codes and message bodies.
        logger.warning("Brevo delivery failed: %s", type(exc).__name__)
        return False


def _sender(value: str) -> dict:
    name, address = parseaddr(value or "")
    sender = {"email": address or getattr(settings, "DEFAULT_FROM_EMAIL", "")}
    if name:
        sender["name"] = name
    return sender


class BrevoEmailBackend(BaseEmailBackend):
    """Django email backend that speaks Brevo's transactional email API."""

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            payload = {
                "sender": _sender(message.from_email or settings.DEFAULT_FROM_EMAIL),
                "to": [{"email": address} for address in message.to],
                "subject": message.subject,
                "textContent": message.body,
            }
            if message.cc:
                payload["cc"] = [{"email": address} for address in message.cc]
            if message.bcc:
                payload["bcc"] = [{"email": address} for address in message.bcc]
            html = next((body for body, mimetype in message.alternatives if mimetype == "text/html"), None)
            if html:
                payload["htmlContent"] = html
            if _post(EMAIL_ENDPOINT, payload, timeout=getattr(settings, "EMAIL_TIMEOUT", 10)):
                sent += 1
            elif not self.fail_silently:
                logger.warning("Brevo email was not accepted for %s", message.to)
        return sent


def send_transactional_sms(phone_number: str, body: str) -> bool:
    """Send one transactional SMS. Returns False when the provider rejects it."""
    if not E164.match(phone_number or ""):
        logger.info("SMS skipped: recipient is not E.164")
        return False
    payload = {
        "sender": getattr(settings, "BREVO_SMS_SENDER", "Blasa"),
        "recipient": phone_number.lstrip("+"),
        "content": body,
        "type": "transactional",
    }
    return _post(SMS_ENDPOINT, payload, timeout=getattr(settings, "SMS_TIMEOUT", 10))
