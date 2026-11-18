from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class VerificationDocument(TimeStampedModel):
    class DocType(models.TextChoices):
        CIN = "CIN", "National ID (CIN)"
        CARTE_GRISE = "CARTE_GRISE", "Vehicle registration (Carte Grise)"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending review"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        NEEDS_MORE_DOCUMENTS = "NEEDS_MORE_DOCUMENTS", "Needs more documents"
        SUPERSEDED = "SUPERSEDED", "Superseded"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=20, choices=DocType.choices)
    storage_key = models.CharField(max_length=512)  # Object key only, never a public URL
    content_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveIntegerField(null=True, blank=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)  # Set only after storage validation
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "doc_type"])]


class VerificationReview(models.Model):
    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Approve"
        REJECT = "REJECT", "Reject"
        REQUEST_MORE = "REQUEST_MORE", "Request more documents"
        SUPERSEDE = "SUPERSEDE", "Supersede"
        REVOKE = "REVOKE", "Revoke"

    document = models.ForeignKey(VerificationDocument, on_delete=models.PROTECT, related_name="history")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    decision = models.CharField(max_length=20, choices=Decision.choices)
    reason = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp", "id"]
