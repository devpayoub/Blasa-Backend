from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class OtpCode(TimeStampedModel):
    """One-time SMS login code. Only the hash is stored; codes are single-use and short-lived."""

    phone_number = models.CharField(max_length=20, db_index=True)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["phone_number", "created_at"])]


class ContactOtp(TimeStampedModel):
    """Purpose-, account- and session-bound contact proof; never stores plaintext codes."""

    class Channel(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        PHONE = "PHONE", "Phone"

    class Purpose(models.TextChoices):
        LOGIN = "LOGIN", "Login"
        LINK = "LINK", "Link"

    channel = models.CharField(max_length=5, choices=Channel.choices)
    contact = models.CharField(max_length=254)
    purpose = models.CharField(max_length=5, choices=Purpose.choices)
    initiating_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="contact_otps",
    )
    session_hash = models.CharField(max_length=64, blank=True, default="")
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["channel", "contact", "created_at"])]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(purpose="LOGIN", channel="EMAIL", initiating_user__isnull=True, session_hash="")
                    | models.Q(purpose="LINK", initiating_user__isnull=False)
                ),
                name="contact_otp_purpose_user",
            ),
            models.CheckConstraint(
                condition=models.Q(channel__in=["EMAIL", "PHONE"]),
                name="contact_otp_channel_valid",
            ),
        ]
