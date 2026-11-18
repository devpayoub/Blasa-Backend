from django.db import transaction

from apps.verification.models import VerificationDocument
from core.events import publish

from .models import User


def _has_approved_cin(user_id):
    return VerificationDocument.objects.filter(
        user_id=user_id, doc_type=VerificationDocument.DocType.CIN,
        status=VerificationDocument.Status.APPROVED,
    ).exists()


@transaction.atomic
def upgrade_to_driver(user: User) -> User:
    """Only a passenger with an actual approved CIN may become a driver; never trust a cached flag."""
    locked = User.objects.select_for_update().get(pk=user.pk)
    if locked.role != User.Role.PASSENGER:
        raise ValueError("Only passengers can become drivers")
    if not _has_approved_cin(locked.pk):
        raise ValueError("Your CIN must be approved before becoming a driver")
    locked.role = User.Role.DRIVER
    locked.is_verified = True
    locked.save(update_fields=["role", "is_verified", "updated_at"])
    publish("user.became_driver", user_id=locked.pk)
    return locked


@transaction.atomic
def handle_verification_changed(*, document_id, user_id, doc_type, **_):
    """Recompute identity for every CIN status transition, including rejection and supersession."""
    if doc_type == VerificationDocument.DocType.CIN:
        user = User.objects.select_for_update().get(pk=user_id)
        verified = _has_approved_cin(user_id)
        if user.is_verified != verified:
            user.is_verified = verified
            user.save(update_fields=["is_verified", "updated_at"])
