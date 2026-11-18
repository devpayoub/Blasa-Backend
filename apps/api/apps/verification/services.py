from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.audit.services import record_action
from core.events import publish

from .models import VerificationDocument, VerificationReview
from .storage import snapshot_upload, validate_storage_key, validate_stored_document, validate_upload_metadata


def ensure_uploads_enabled():
    if not getattr(settings, "IDENTITY_DOCUMENT_UPLOADS_ENABLED", False):
        raise PermissionDenied("Identity document uploads are disabled pending legal sign-off")


def _lock_document(document):
    # All eligibility mutations lock owner first, then document. This serializes CIN replacement
    # and driver upgrades even when two different documents of the same owner are reviewed.
    user = get_user_model().objects.select_for_update().get(pk=document.user_id)
    return user, VerificationDocument.objects.select_for_update().get(pk=document.pk, user=user)


def _require_staff(reviewer):
    if not reviewer.is_authenticated or not reviewer.is_active or not reviewer.is_staff:
        raise PermissionDenied("Only staff may review or revoke documents")


@transaction.atomic
def submit_document(*, user, doc_type, storage_key, content_type, size_bytes):
    ensure_uploads_enabled()
    if doc_type not in VerificationDocument.DocType.values:
        raise ValueError("Invalid document type")
    validate_storage_key(storage_key=storage_key, user_id=user.pk, doc_type=doc_type)
    validate_upload_metadata(content_type=content_type, size_bytes=size_bytes, file_name=storage_key)
    doc = VerificationDocument.objects.create(
        user=user, doc_type=doc_type, storage_key=storage_key, content_type=content_type, size_bytes=size_bytes,
    )
    publish("verification.submitted", document_id=str(doc.id))
    return doc


@transaction.atomic
def confirm_upload(*, document, actor):
    ensure_uploads_enabled()
    if actor.pk != document.user_id and not actor.is_staff:
        raise PermissionDenied("Only the owner or staff may confirm an upload")
    _, document = _lock_document(document)
    if document.uploaded_at is not None:
        return document
    if document.status not in {VerificationDocument.Status.PENDING, VerificationDocument.Status.NEEDS_MORE_DOCUMENTS}:
        raise ValueError("Document is not awaiting an upload")
    storage, etag = validate_stored_document(document)
    document.storage_key = snapshot_upload(storage, document.storage_key, etag)
    document.uploaded_at = timezone.now()
    document.save(update_fields=["storage_key", "uploaded_at", "updated_at"])
    return document


def _record_decision(document, reviewer, decision, status, reason):
    document.status = status
    document.reviewed_by = reviewer
    document.reviewed_at = timezone.now()
    document.rejection_reason = "" if decision == VerificationReview.Decision.APPROVE else reason
    document.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])
    review = VerificationReview.objects.create(document=document, actor=reviewer, decision=decision, reason=reason)
    record_action(
        actor=reviewer, action=f"verification.{decision.lower()}", object_type="verification_document",
        object_id=document.pk, reason=reason,
        metadata={"doc_type": document.doc_type, "resulting_status": status, "owner_id": str(document.user_id),
                  "review_id": str(review.pk)},
    )
    publish(
        "verification.changed", document_id=str(document.pk), user_id=document.user_id,
        doc_type=document.doc_type, status=document.status, review_id=str(review.pk),
    )


@transaction.atomic
def review_document(*, document, reviewer, decision, reason: str = ""):
    _require_staff(reviewer)
    statuses = {
        VerificationReview.Decision.APPROVE: VerificationDocument.Status.APPROVED,
        VerificationReview.Decision.REJECT: VerificationDocument.Status.REJECTED,
        VerificationReview.Decision.REQUEST_MORE: VerificationDocument.Status.NEEDS_MORE_DOCUMENTS,
    }
    if decision not in statuses:
        raise ValueError("Decision must be APPROVE, REJECT or REQUEST_MORE")
    reason = reason.strip()
    if decision != VerificationReview.Decision.APPROVE and not reason:
        raise ValueError("A reason is required for rejection or requesting more documents")
    _, document = _lock_document(document)
    if document.status not in {VerificationDocument.Status.PENDING, VerificationDocument.Status.NEEDS_MORE_DOCUMENTS}:
        raise ValueError("Document has already been reviewed; use revoke for an approved document")
    if decision == VerificationReview.Decision.APPROVE:
        if document.uploaded_at is None:
            raise ValueError("Only a confirmed upload can be approved")
        # PUT URLs remain usable until expiry: recheck the actual object at approval as well.
        validate_stored_document(document)
        if document.doc_type == VerificationDocument.DocType.CIN:
            previous = VerificationDocument.objects.select_for_update().filter(
                user_id=document.user_id, doc_type=VerificationDocument.DocType.CIN,
                status=VerificationDocument.Status.APPROVED,
            ).exclude(pk=document.pk).order_by("pk")
            for old in previous:
                _record_decision(
                    old, reviewer, VerificationReview.Decision.SUPERSEDE,
                    VerificationDocument.Status.SUPERSEDED, f"Replaced by approved CIN {document.pk}",
                )
    _record_decision(document, reviewer, decision, statuses[decision], reason)
    return document


@transaction.atomic
def revoke_document(*, document, reviewer, reason):
    _require_staff(reviewer)
    reason = reason.strip()
    if not reason:
        raise ValueError("A reason is required for revocation")
    _, document = _lock_document(document)
    if document.status != VerificationDocument.Status.APPROVED:
        raise ValueError("Only an approved document can be revoked")
    _record_decision(
        document, reviewer, VerificationReview.Decision.REVOKE, VerificationDocument.Status.REJECTED, reason,
    )
    return document
