from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from django.db import close_old_connections
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from apps.users.models import User
from apps.users.services import handle_verification_changed, upgrade_to_driver
from apps.verification.models import VerificationDocument, VerificationReview
from apps.verification.services import confirm_upload, review_document, revoke_document, submit_document
from apps.verification.storage import MAX_UPLOAD_BYTES, signed_upload_url

pytestmark = pytest.mark.django_db
PDF = b"%PDF-1.7 synthetic identity"
JPEG = b"\xff\xd8\xff\xe0synthetic identity"
PNG = b"\x89PNG\r\n\x1a\nsynthetic identity"
BASE = "/api/v1/verification/documents/"


@pytest.fixture(autouse=True)
def enable_synthetic_uploads(settings):
    settings.IDENTITY_DOCUMENT_UPLOADS_ENABLED = True


@pytest.fixture
def owner():
    return User.objects.create_user(phone_number="+21621000100")


@pytest.fixture
def staff():
    return User.objects.create_user(phone_number="+21621000101", is_staff=True)


def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def document(owner, *, doc_type="CIN", content_type="application/pdf", payload=PDF, suffix="pdf", uploaded=False):
    return VerificationDocument.objects.create(
        user=owner, doc_type=doc_type,
        storage_key=f"verification/{owner.pk}/{doc_type.lower()}/synthetic-{uuid4().hex}.{suffix}",
        content_type=content_type, size_bytes=len(payload),
        uploaded_at=timezone.now() if uploaded else None,
    )


def stored(s3, *, content_type="application/pdf", payload=PDF, size=None):
    s3.head_object.return_value = {
        "ContentLength": len(payload) if size is None else size, "ContentType": content_type, "ETag": '"synthetic"',
    }
    s3.get_object.side_effect = lambda **_: {
        "Body": BytesIO(payload), "ContentType": content_type, "ETag": '"synthetic"',
    }


def approved(owner, staff):
    doc = document(owner)
    with patch("apps.verification.storage.client") as mock_s3:
        stored(mock_s3.return_value)
        doc = confirm_upload(document=doc, actor=owner)
        return review_document(document=doc, reviewer=staff, decision="APPROVE")


@pytest.mark.parametrize("changes", [
    {"content_type": "text/html"}, {"content_type": "image/gif"}, {"content_type": ""},
    {"content_type": None}, {"file_name": "identity.exe"}, {"file_name": "identity"},
    {"file_name": "identity.png"}, {"size_bytes": 0}, {"size_bytes": -1},
    {"size_bytes": MAX_UPLOAD_BYTES + 1}, {"size_bytes": True}, {"size_bytes": 1.5},
    {"size_bytes": None},
])
def test_upload_rejects_invalid_metadata(owner, changes):
    payload = {"doc_type": "CIN", "file_name": "identity.pdf", "content_type": "application/pdf", "size_bytes": len(PDF)}
    payload.update(changes)
    with patch("apps.verification.storage.client") as mock_s3:
        response = api(owner).post(BASE + "upload-url/", payload, format="json")
    assert response.status_code == 400
    assert VerificationDocument.objects.count() == 0
    mock_s3.assert_not_called()


@pytest.mark.parametrize("missing", ["content_type", "size_bytes"])
def test_upload_requires_mime_and_size(owner, missing):
    payload = {"doc_type": "CIN", "file_name": "identity.pdf", "content_type": "application/pdf", "size_bytes": len(PDF)}
    del payload[missing]
    assert api(owner).post(BASE + "upload-url/", payload, format="json").status_code == 400


@pytest.mark.parametrize("content_type,suffix,payload", [
    ("application/pdf", "PDF", PDF), ("image/jpeg", "jpg", JPEG),
    ("image/jpeg", "jpeg", JPEG), ("image/png", "png", PNG),
])
def test_supported_types_validate_actual_objects(owner, content_type, suffix, payload):
    client = api(owner)
    with patch("apps.verification.storage.client") as mock_s3:
        mock_s3.return_value.generate_presigned_url.return_value = "https://storage.test/put"
        response = client.post(BASE + "upload-url/", {
            "doc_type": "CIN", "file_name": f"identity.{suffix}", "content_type": content_type, "size_bytes": len(payload),
        }, format="json")
        assert response.status_code == 201
        stored(mock_s3.return_value, content_type=content_type, payload=payload)
        confirmation = client.post(BASE + f"{response.data['document_id']}/confirm-upload/")
        assert confirmation.status_code == 200 and confirmation.data["uploaded_at"]
        assert "storage_key" not in confirmation.data


def test_ten_mib_boundary_is_accepted(owner):
    with patch("apps.verification.storage.client") as mock_s3:
        mock_s3.return_value.generate_presigned_url.return_value = "https://storage.test/put"
        response = api(owner).post(BASE + "upload-url/", {
            "doc_type": "CIN", "file_name": "identity.pdf", "content_type": "application/pdf", "size_bytes": MAX_UPLOAD_BYTES,
        }, format="json")
    assert response.status_code == 201


def test_legal_gate_blocks_upload_and_confirmation_without_storage_calls(owner, settings):
    doc = document(owner)
    settings.IDENTITY_DOCUMENT_UPLOADS_ENABLED = False
    with patch("apps.verification.storage.client") as mock_s3:
        assert api(owner).post(BASE + "upload-url/", {}, format="json").status_code == 403
        assert api(owner).post(BASE + f"{doc.pk}/confirm-upload/").status_code == 403
        with pytest.raises(PermissionDenied):
            confirm_upload(document=doc, actor=owner)
        with pytest.raises(PermissionDenied):
            submit_document(
                user=owner, doc_type="CIN", storage_key=doc.storage_key, content_type="application/pdf", size_bytes=len(PDF),
            )
    mock_s3.assert_not_called()
    doc.refresh_from_db()
    assert doc.uploaded_at is None
    assert VerificationDocument.objects.count() == 1


@pytest.mark.parametrize("problem", ["size", "large", "type", "magic", "missing", "invalid", "etag", "changed"])
def test_confirm_rejects_invalid_storage_without_timestamp_or_audit(owner, problem):
    doc = document(owner)
    with patch("apps.verification.storage.client") as mock_s3:
        s3 = mock_s3.return_value
        stored(s3)
        if problem == "size":
            s3.head_object.return_value["ContentLength"] += 1
        elif problem == "large":
            s3.head_object.return_value["ContentLength"] = MAX_UPLOAD_BYTES + 1
        elif problem == "type":
            s3.head_object.return_value["ContentType"] = "image/jpeg"
        elif problem == "magic":
            stored(s3, payload=b"<html>spoofed PDF</html>", size=doc.size_bytes)
        elif problem in {"missing", "invalid"}:
            s3.head_object.side_effect = ClientError(
                {"Error": {"Code": "NoSuchKey" if problem == "missing" else "InvalidObjectState"}}, "HeadObject",
            )
        elif problem == "etag":
            del s3.head_object.return_value["ETag"]
        elif problem == "changed":
            s3.get_object.side_effect = ClientError({"Error": {"Code": "PreconditionFailed"}}, "GetObject")
        response = api(owner).post(BASE + f"{doc.pk}/confirm-upload/")
    assert response.status_code == 400
    doc.refresh_from_db()
    assert doc.uploaded_at is None and doc.status == "PENDING"
    assert doc.history.count() == 0
    owner.refresh_from_db()
    assert owner.is_verified is False


@pytest.mark.parametrize("content_type,suffix,payload", [("image/jpeg", "jpg", JPEG), ("image/png", "png", PNG)])
def test_images_cannot_spoof_file_magic(owner, content_type, suffix, payload):
    doc = document(owner, content_type=content_type, suffix=suffix, payload=payload)
    with patch("apps.verification.storage.client") as mock_s3:
        stored(mock_s3.return_value, content_type=content_type, payload=PDF, size=doc.size_bytes)
        response = api(owner).post(BASE + f"{doc.pk}/confirm-upload/")
    assert response.status_code == 400
    doc.refresh_from_db()
    assert doc.uploaded_at is None


def test_storage_ownership_prefix_is_enforced_by_services_and_signer(owner):
    wrong_key = "verification/someone-else/cin/identity.pdf"
    with patch("apps.verification.storage.client") as mock_s3:
        with pytest.raises(ValueError):
            signed_upload_url(wrong_key, content_type="application/pdf", size_bytes=len(PDF), user_id=owner.pk, doc_type="CIN")
        with pytest.raises(ValueError):
            submit_document(user=owner, doc_type="CIN", storage_key=wrong_key, content_type="application/pdf", size_bytes=len(PDF))
        doc = document(owner)
        doc.storage_key = wrong_key
        doc.save(update_fields=["storage_key"])
        assert api(owner).post(BASE + f"{doc.pk}/confirm-upload/").status_code == 400
    mock_s3.assert_not_called()
    doc.refresh_from_db()
    assert doc.uploaded_at is None


@pytest.mark.parametrize("action,payload", [
    ("review", {"decision": "APPROVE"}), ("review", {"decision": "REJECT", "reason": "No"}),
    ("revoke", {"reason": "No"}),
])
def test_review_and_revoke_apis_are_staff_only(owner, action, payload):
    doc = document(owner)
    stranger = User.objects.create_user(phone_number="+21621000102")
    for actor in (owner, stranger):
        assert api(actor).post(BASE + f"{doc.pk}/{action}/", payload, format="json").status_code == 403
    doc.refresh_from_db()
    assert doc.status == "PENDING" and not doc.history.exists()


def test_nonstaff_services_cannot_review_or_revoke(owner):
    doc = document(owner, uploaded=True)
    with patch("apps.verification.storage.client") as mock_s3:
        with pytest.raises(PermissionDenied):
            review_document(document=doc, reviewer=owner, decision="APPROVE")
        with pytest.raises(PermissionDenied):
            revoke_document(document=doc, reviewer=owner, reason="No")
    mock_s3.assert_not_called()
    assert not doc.history.exists()


def test_approval_requires_confirmation_and_rechecks_storage(owner, staff):
    doc = document(owner)
    with patch("apps.verification.storage.client") as mock_s3:
        refused = api(staff).post(BASE + f"{doc.pk}/review/", {"decision": "APPROVE"}, format="json")
        assert refused.status_code == 400
        mock_s3.assert_not_called()
        stored(mock_s3.return_value)
        doc = confirm_upload(document=doc, actor=owner)
        mock_s3.return_value.head_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "HeadObject")
        missing = api(staff).post(BASE + f"{doc.pk}/review/", {"decision": "APPROVE"}, format="json")
    assert missing.status_code == 400
    doc.refresh_from_db()
    assert doc.status == "PENDING" and not doc.history.exists()


def test_staff_review_audit_is_visible_to_owner_but_not_stranger(owner, staff):
    doc = document(owner)
    with patch("apps.verification.storage.client") as mock_s3:
        stored(mock_s3.return_value)
        doc = confirm_upload(document=doc, actor=owner)
        response = api(staff).post(BASE + f"{doc.pk}/review/", {"decision": "APPROVE", "reason": "Checked identity"}, format="json")
    assert response.status_code == 200 and response.data["status"] == "APPROVED"
    history = response.data["history"]
    assert len(history) == 1
    assert str(history[0]["actor"]) == str(staff.pk)
    assert history[0]["decision"] == "APPROVE" and history[0]["reason"] == "Checked identity" and history[0]["timestamp"]
    owner_response = api(owner).get(BASE + f"{doc.pk}/")
    assert owner_response.status_code == 200 and owner_response.data["history"] == history
    assert "storage_key" not in owner_response.data
    stranger = User.objects.create_user(phone_number="+21621000102")
    assert api(stranger).get(BASE + f"{doc.pk}/").status_code == 404
    assert api(owner).delete(BASE + f"{doc.pk}/").status_code == 405
    owner.refresh_from_db()
    assert owner.is_verified is True


@pytest.mark.parametrize("decision", ["REJECT", "REQUEST_MORE"])
def test_negative_review_requires_reason_and_records_status(owner, staff, decision):
    doc = document(owner)
    client = api(staff)
    assert client.post(BASE + f"{doc.pk}/review/", {"decision": decision}, format="json").status_code == 400
    response = client.post(BASE + f"{doc.pk}/review/", {"decision": decision, "reason": "Unreadable identity"}, format="json")
    assert response.status_code == 200
    assert response.data["status"] == ("REJECTED" if decision == "REJECT" else "NEEDS_MORE_DOCUMENTS")
    assert response.data["rejection_reason"] == "Unreadable identity"
    assert response.data["history"][0]["decision"] == decision
    owner.refresh_from_db()
    assert owner.is_verified is False


def test_requested_document_can_be_confirmed_and_approved_with_history(owner, staff):
    doc = document(owner)
    doc = review_document(document=doc, reviewer=staff, decision="REQUEST_MORE", reason="Upload readable proof")
    with patch("apps.verification.storage.client") as mock_s3:
        stored(mock_s3.return_value)
        doc = confirm_upload(document=doc, actor=owner)
        doc = review_document(document=doc, reviewer=staff, decision="APPROVE")
    assert doc.status == "APPROVED"
    assert list(doc.history.values_list("decision", flat=True)) == ["REQUEST_MORE", "APPROVE"]


def test_cin_replacement_supersedes_only_on_approval_and_revocation_recomputes(owner, staff):
    old = approved(owner, staff)
    replacement = submit_document(
        user=owner, doc_type="CIN", storage_key=f"verification/{owner.pk}/cin/replacement.pdf",
        content_type="application/pdf", size_bytes=len(PDF),
    )
    owner.refresh_from_db()
    old.refresh_from_db()
    assert owner.is_verified and old.status == "APPROVED"
    with patch("apps.verification.storage.client") as mock_s3:
        stored(mock_s3.return_value)
        replacement = confirm_upload(document=replacement, actor=owner)
        owner.refresh_from_db()
        old.refresh_from_db()
        assert owner.is_verified and old.status == "APPROVED"
        replacement = review_document(document=replacement, reviewer=staff, decision="APPROVE")
    old.refresh_from_db()
    owner.refresh_from_db()
    assert old.status == "SUPERSEDED" and replacement.status == "APPROVED" and owner.is_verified
    assert VerificationDocument.objects.filter(user=owner, doc_type="CIN", status="APPROVED").count() == 1
    assert list(old.history.values_list("decision", flat=True)) == ["APPROVE", "SUPERSEDE"]
    assert old.history.last().actor == staff and old.history.last().reason
    assert api(staff).post(BASE + f"{replacement.pk}/revoke/", {}, format="json").status_code == 400
    response = api(staff).post(BASE + f"{replacement.pk}/revoke/", {"reason": "Identity invalidated"}, format="json")
    assert response.status_code == 200 and response.data["status"] == "REJECTED"
    assert response.data["history"][-1]["decision"] == "REVOKE"
    owner.refresh_from_db()
    assert owner.is_verified is False
    assert api(staff).post(BASE + f"{replacement.pk}/revoke/", {"reason": "Again"}, format="json").status_code == 400
    with pytest.raises(ValueError):
        upgrade_to_driver(owner)


@pytest.mark.parametrize("decision", ["REJECT", "REQUEST_MORE"])
def test_pending_replacement_negative_review_does_not_revoke_existing_identity(owner, staff, decision):
    old = approved(owner, staff)
    new = document(owner)
    review_document(document=new, reviewer=staff, decision=decision, reason="Unreadable replacement")
    owner.refresh_from_db()
    old.refresh_from_db()
    assert owner.is_verified and old.status == "APPROVED"


def test_stale_document_instance_cannot_approve_twice(owner, staff):
    doc = document(owner)
    with patch("apps.verification.storage.client") as mock_s3:
        stored(mock_s3.return_value)
        confirmed = confirm_upload(document=doc, actor=owner)
        stale = VerificationDocument.objects.get(pk=doc.pk)
        review_document(document=confirmed, reviewer=staff, decision="APPROVE")
        with pytest.raises(ValueError):
            review_document(document=stale, reviewer=staff, decision="APPROVE")
    assert doc.history.count() == 1


def test_subscriber_recomputes_false_for_rejection_and_preserves_true_for_other_approved_cin(owner):
    owner.is_verified = True
    owner.save(update_fields=["is_verified"])
    doc = document(owner)
    handle_verification_changed(document_id=str(doc.pk), user_id=owner.pk, doc_type="CIN", status="REJECTED")
    owner.refresh_from_db()
    assert owner.is_verified is False
    doc.status = "APPROVED"
    doc.save(update_fields=["status"])
    handle_verification_changed(document_id=str(doc.pk), user_id=owner.pk, doc_type="CIN", status="SUPERSEDED")
    owner.refresh_from_db()
    assert owner.is_verified is True


def test_upgrade_uses_actual_cin_not_stale_user_flag(owner, staff):
    owner.is_verified = True
    owner.save(update_fields=["is_verified"])
    with pytest.raises(ValueError):
        upgrade_to_driver(owner)
    approved(owner, staff)
    owner.is_verified = False
    owner.save(update_fields=["is_verified"])
    driver = upgrade_to_driver(owner)
    assert driver.role == User.Role.DRIVER and driver.is_verified is True


@pytest.mark.parametrize("role", ["BRAND", "DRIVER"])
def test_only_passengers_can_upgrade_even_with_approved_cin(owner, staff, role):
    approved(owner, staff)
    owner.role = role
    owner.save(update_fields=["role"])
    response = api(owner).post("/api/v1/users/me/become-driver/", {}, format="json")
    assert response.status_code == 400
    owner.refresh_from_db()
    assert owner.role == role
    with pytest.raises(ValueError):
        upgrade_to_driver(owner)


def test_invalid_review_decision_cannot_mutate_document(owner, staff):
    doc = document(owner)
    assert api(staff).post(BASE + f"{doc.pk}/review/", {"decision": "SUPERSEDE", "reason": "No"}, format="json").status_code == 400
    assert not VerificationReview.objects.filter(document=doc).exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_cin_approvals_serialize_on_owner_and_keep_one_approved():
    owner = User.objects.create_user(phone_number="+21621000110")
    staff = User.objects.create_user(phone_number="+21621000111", is_staff=True)
    first = document(owner)
    second = document(owner)
    with patch("apps.verification.storage.client") as mock_s3:
        stored(mock_s3.return_value)
        first = confirm_upload(document=first, actor=owner)
        second = confirm_upload(document=second, actor=owner)
        barrier = Barrier(2, timeout=10)

        def approve_concurrently(document_id):
            close_old_connections()
            try:
                doc = VerificationDocument.objects.get(pk=document_id)
                reviewer = User.objects.get(pk=staff.pk)
                barrier.wait()
                return review_document(document=doc, reviewer=reviewer, decision="APPROVE").pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(approve_concurrently, doc.pk) for doc in (first, second)]
            assert {future.result(timeout=20) for future in futures} == {first.pk, second.pk}
    assert VerificationDocument.objects.filter(user=owner, doc_type="CIN", status="APPROVED").count() == 1
    assert VerificationDocument.objects.filter(user=owner, doc_type="CIN", status="SUPERSEDED").count() == 1
    assert VerificationReview.objects.filter(document__user=owner, decision="APPROVE").count() == 2
    assert VerificationReview.objects.filter(document__user=owner, decision="SUPERSEDE").count() == 1
    owner.refresh_from_db()
    assert owner.is_verified is True


@pytest.mark.parametrize("code", ["404", "NotFound", "NoSuchKey", "InvalidRange", "InvalidObjectState"])
def test_range_get_invalid_object_returns_400_without_confirmation(owner, code):
    doc = document(owner)
    with patch("apps.verification.storage.client") as mock_s3:
        stored(mock_s3.return_value)
        mock_s3.return_value.get_object.side_effect = ClientError({"Error": {"Code": code}}, "GetObject")
        response = api(owner).post(BASE + f"{doc.pk}/confirm-upload/")
    assert response.status_code == 400
    doc.refresh_from_db()
    assert doc.uploaded_at is None


def test_staff_can_review_other_owners_documents_but_nonexistent_is_404(owner, staff):
    doc = document(owner)
    response = api(staff).post(BASE + f"{doc.pk}/review/", {"decision": "REJECT", "reason": "Wrong document"}, format="json")
    assert response.status_code == 200
    assert api(staff).post(BASE + f"{uuid4()}/review/", {"decision": "APPROVE"}, format="json").status_code == 404


def test_confirm_service_rejects_stranger(owner):
    doc = document(owner)
    stranger = User.objects.create_user(phone_number="+21621000102")
    with patch("apps.verification.storage.client") as mock_s3:
        with pytest.raises(PermissionDenied):
            confirm_upload(document=doc, actor=stranger)
    mock_s3.assert_not_called()
