from django.conf import settings
from django.db import models

from apps.wallet.constraints import ImmutableQuerySet
from core.models import TimeStampedModel


class AuditLog(TimeStampedModel):
    """Append-only trace of privileged activity.

    Two writers exist:
    - AdminAuditMiddleware records every /admin/ request (method/path/status).
    - record_action() records a deliberate staff decision (review, ban, funding,
      override) with the actor, object and decision metadata — never request
      bodies, OTPs, tokens, documents or payment credentials.
    """

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=500)
    status_code = models.PositiveSmallIntegerField()
    request_id = models.CharField(max_length=64, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)  # Hashed, not raw IP
    # Structured staff-decision columns. Empty for plain request traces.
    action = models.CharField(max_length=60, blank=True, default="", db_index=True)
    object_type = models.CharField(max_length=40, blank=True, default="")
    object_id = models.CharField(max_length=64, blank=True, default="")
    reason = models.CharField(max_length=200, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["object_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.action or self.method} {self.object_type}:{self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk and AuditLog.objects.filter(pk=self.pk).exists():
            raise PermissionError("Audit logs are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit logs cannot be deleted")
