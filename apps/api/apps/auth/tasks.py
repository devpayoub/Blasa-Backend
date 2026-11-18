import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from .sms import send_sms

logger = logging.getLogger("blasa.auth.delivery")


def _message(code, language):
    messages = {
        "en": f"Blasa: your verification code is {code}. Valid for 5 minutes.",
        "fr": f"Blasa : votre code de vérification est {code}. Valable 5 minutes.",
        "ar": f"Blasa: رمز التحقق الخاص بك هو {code}. صالح لمدة 5 دقائق.",
    }
    return messages.get(language, messages["en"])


@shared_task(ignore_result=True)
def send_otp_sms(*, phone_number: str, code: str, language: str = "en") -> None:
    send_sms(phone_number, _message(code, language))


@shared_task(ignore_result=True)
def send_otp_email(*, email: str, code: str, language: str = "en") -> None:
    backend = settings.EMAIL_BACKEND
    # Console/file backends must never expose real authentication secrets. With
    # no SMTP host configured, development dispatch is intentionally a no-op.
    if backend in {
        "django.core.mail.backends.console.EmailBackend",
        "django.core.mail.backends.filebased.EmailBackend",
        "django.core.mail.backends.dummy.EmailBackend",
    } or (backend == "django.core.mail.backends.smtp.EmailBackend" and not settings.EMAIL_HOST):
        logger.info("Email verification dispatch skipped: transport not configured")
        return
    subject = {"en": "Blasa verification code", "fr": "Code de vérification Blasa", "ar": "رمز التحقق Blasa"}.get(
        language, "Blasa verification code"
    )
    send_mail(subject, _message(code, language), settings.DEFAULT_FROM_EMAIL, [email])
