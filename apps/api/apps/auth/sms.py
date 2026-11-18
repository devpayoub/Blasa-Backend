import logging
import re

from django.conf import settings

logger = logging.getLogger("blasa.sms")


def send_sms(phone_number: str, body: str) -> None:
    """Dispatch one SMS through the configured provider.

    Brevo is the production adapter (Render's free tier blocks outbound SMTP and
    has no direct SMS product). With no credentials the call is a no-op that
    logs only the provider name — never the message or the code.
    """
    provider = getattr(settings, "SMS_PROVIDER", "console")
    recipient = phone_number if re.fullmatch(r"\+216[0-9]{8}", phone_number) else "[invalid]"
    if provider != "brevo":
        logger.info("[sms:%s] dispatch skipped -> %s", provider, recipient)
        return
    from core.delivery.brevo import send_transactional_sms

    if not send_transactional_sms(phone_number, body):
        logger.warning("[sms:brevo] delivery not accepted -> %s", recipient)
