from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Report(TimeStampedModel):
    """A user-filed report against a user, ride, vehicle, or chat message."""

    class TargetType(models.TextChoices):
        USER = "USER", "User"
        RIDE = "RIDE", "Ride"
        VEHICLE = "VEHICLE", "Vehicle"
        MESSAGE = "MESSAGE", "Message"

    class Reason(models.TextChoices):
        HARASSMENT = "HARASSMENT", "Harassment"
        FRAUD = "FRAUD", "Fraud"
        SAFETY = "SAFETY", "Safety"
        SPAM = "SPAM", "Spam"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        DISMISSED = "DISMISSED", "Dismissed"
        ACTIONED = "ACTIONED", "Actioned"

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reports_filed"
    )
    target_type = models.CharField(max_length=10, choices=TargetType.choices)
    target_id = models.CharField(max_length=64)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    details = models.TextField(max_length=2000, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports_handled"
    )
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["target_type", "target_id"])]
        constraints = [
            models.UniqueConstraint(
                fields=["reporter", "target_type", "target_id", "status"],
                condition=models.Q(status="OPEN"),
                name="moderation_report_one_open_per_target",
            )
        ]


class Ban(TimeStampedModel):
    """A moderation ban; while active, the user cannot authenticate at all."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bans")
    reason = models.TextField(max_length=2000)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="bans_issued"
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="bans_revoked"
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(revoked_at__isnull=True),
                name="moderation_ban_one_active_per_user",
            )
        ]

    def __str__(self):
        return f"ban:{self.user_id}"
