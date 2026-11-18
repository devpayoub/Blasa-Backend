from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class NotificationPreference(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences")
    push_enabled = models.BooleanField(default=False)
    email_enabled = models.BooleanField(default=False)
    sms_enabled = models.BooleanField(default=False)
    promotions_enabled = models.BooleanField(default=False)


class DeviceToken(TimeStampedModel):
    class Provider(models.TextChoices):
        FCM = "FCM", "Firebase"
        EXPO = "EXPO", "Expo"

    class Platform(models.TextChoices):
        IOS = "IOS", "iOS"
        ANDROID = "ANDROID", "Android"
        WEB = "WEB", "Web"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="device_tokens")
    token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(max_length=10, choices=Platform.choices, default=Platform.ANDROID)
    provider = models.CharField(max_length=10, choices=Provider.choices, default=Provider.FCM)

    def __str__(self):
        return f"{self.user}#{self.platform}"


class Notification(TimeStampedModel):
    """One in-app inbox entry. Push/SMS deliveries reference this via payload."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notifications")
    kind = models.CharField(max_length=60, db_index=True)  # e.g. "booking.status_changed"
    title = models.CharField(max_length=150)
    body = models.CharField(max_length=500)
    payload = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    event_key = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "read_at"])]
        constraints = [models.UniqueConstraint(
            fields=["user", "event_key"], condition=~models.Q(event_key=""), name="notification_event_recipient_unique",
        )]


class NotificationDelivery(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENDING = "SENDING", "Sending"
        SENT = "SENT", "Accepted by transport"
        BLOCKED = "BLOCKED", "Transport not configured"
        FAILED = "FAILED", "Retries exhausted"
        SKIPPED = "SKIPPED", "No longer eligible"

    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.CharField(max_length=10, choices=[("push", "Push"), ("email", "Email"), ("sms", "SMS")])
    destination = models.CharField(max_length=255)
    provider = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error = models.CharField(max_length=120, blank=True)
    external_id = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["notification", "channel", "destination"], name="notification_delivery_unique",
        )]
