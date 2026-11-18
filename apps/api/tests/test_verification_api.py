from io import BytesIO
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from rest_framework.test import APIClient

from apps.users.models import User
from apps.verification.models import VerificationDocument
from apps.verification.services import confirm_upload, review_document

JPEG = b"\xff\xd8\xff\xe0synthetic-jpeg"


@pytest.fixture(autouse=True)
def enable_synthetic_uploads(settings):
    settings.IDENTITY_DOCUMENT_UPLOADS_ENABLED = True


def _document(user):
    return VerificationDocument.objects.create(
        user=user, doc_type=VerificationDocument.DocType.CIN,
        storage_key=f"verification/{user.id}/cin/synthetic.jpg",
        content_type="image/jpeg", size_bytes=len(JPEG),
    )


def _mock_jpeg(s3):
    s3.head_object.return_value = {"ContentLength": len(JPEG), "ContentType": "image/jpeg", "ETag": '"synthetic"'}
    s3.get_object.side_effect = lambda **_: {
        "Body": BytesIO(JPEG), "ContentType": "image/jpeg", "ETag": '"synthetic"',
    }


@pytest.mark.django_db
def test_upload_url_creates_pending_document_and_returns_signed_put():
    user = User.objects.create_user(phone_number="+21620000020", password="x")
    client = APIClient()
    client.force_authenticate(user)

    with patch("apps.verification.storage.client") as mock_s3:
        mock_s3.return_value.generate_presigned_url.return_value = "https://s3.test/signed-put"
        response = client.post(
            "/api/v1/verification/documents/upload-url/",
            {"doc_type": "CIN", "file_name": "cin.jpg", "content_type": "image/jpeg", "size_bytes": len(JPEG)},
            format="json",
        )
        assert mock_s3.return_value.generate_presigned_url.call_args.kwargs["Params"]["ContentType"] == "image/jpeg"

    assert response.status_code == 201
    assert response.data["upload_url"] == "https://s3.test/signed-put"
    document = VerificationDocument.objects.get(pk=response.data["document_id"])
    assert document.user == user
    assert document.status == VerificationDocument.Status.PENDING
    assert document.uploaded_at is None
    assert document.content_type == "image/jpeg"
    assert document.size_bytes == len(JPEG)
    assert document.storage_key.startswith(f"verification/{user.id}/cin/")
    assert document.storage_key.endswith(".jpg")


@pytest.mark.django_db
def test_confirm_upload_is_idempotent():
    user = User.objects.create_user(phone_number="+21620000021", password="x")
    document = _document(user)
    client = APIClient()
    client.force_authenticate(user)

    with patch("apps.verification.storage.client") as mock_s3:
        _mock_jpeg(mock_s3.return_value)
        first = client.post(f"/api/v1/verification/documents/{document.id}/confirm-upload/")
        assert first.status_code == 200
        assert first.data["uploaded_at"] is not None
        second = client.post(f"/api/v1/verification/documents/{document.id}/confirm-upload/")
        assert second.status_code == 200
        assert second.data["uploaded_at"] == first.data["uploaded_at"]
        assert mock_s3.return_value.head_object.call_count == 1
        assert mock_s3.return_value.get_object.call_args.kwargs["Range"] == "bytes=0-15"
        assert mock_s3.return_value.get_object.call_args.kwargs["IfMatch"] == '"synthetic"'
        document.refresh_from_db()
        copy = mock_s3.return_value.copy_object.call_args.kwargs
        assert copy["CopySource"]["Key"] != document.storage_key
        assert copy["Key"] == document.storage_key
        assert copy["CopySourceIfMatch"] == '"synthetic"'
        assert mock_s3.return_value.copy_object.call_count == 1


@pytest.mark.django_db
def test_download_url_owner_and_staff_only():
    owner = User.objects.create_user(phone_number="+21620000022", password="x")
    stranger = User.objects.create_user(phone_number="+21620000023", password="x")
    staff = User.objects.create_user(phone_number="+21620000024", password="x", is_staff=True)
    document = _document(owner)
    stranger_client = APIClient()
    stranger_client.force_authenticate(stranger)
    assert stranger_client.get(f"/api/v1/verification/documents/{document.id}/download-url/").status_code == 404
    assert stranger_client.post(f"/api/v1/verification/documents/{document.id}/confirm-upload/").status_code == 404

    with patch("apps.verification.storage.client") as mock_s3:
        mock_s3.return_value.generate_presigned_url.return_value = "https://s3.test/signed-get"
        owner_client = APIClient()
        owner_client.force_authenticate(owner)
        owner_response = owner_client.get(f"/api/v1/verification/documents/{document.id}/download-url/")
        staff_client = APIClient()
        staff_client.force_authenticate(staff)
        staff_response = staff_client.get(f"/api/v1/verification/documents/{document.id}/download-url/")

    assert owner_response.status_code == 200
    assert staff_response.status_code == 200
    assert owner_response.data["download_url"] == "https://s3.test/signed-get"


@pytest.mark.django_db
def test_reviewed_document_cannot_be_reviewed_again():
    user = User.objects.create_user(phone_number="+21620000025", password="x")
    reviewer = User.objects.create_user(phone_number="+21620000026", password="x", is_staff=True)
    document = _document(user)
    with patch("apps.verification.storage.client") as mock_s3:
        _mock_jpeg(mock_s3.return_value)
        document = confirm_upload(document=document, actor=user)
        reviewed = review_document(document=document, reviewer=reviewer, decision="APPROVE")
    assert reviewed.status == VerificationDocument.Status.APPROVED
    assert reviewed.reviewed_by == reviewer
    with pytest.raises(ValueError):
        review_document(document=document, reviewer=reviewer, decision="REJECT", reason="again")


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["request", "confirm", "download"])
def test_storage_transport_failure_is_readable_and_does_not_confirm(operation):
    user = User.objects.create_user(phone_number="+21620000027")
    document = _document(user)
    client = APIClient()
    client.force_authenticate(user)
    with patch("apps.verification.storage.client") as s3:
        s3.return_value.generate_presigned_url.side_effect = NoCredentialsError()
        s3.return_value.head_object.side_effect = EndpointConnectionError(endpoint_url="http://synthetic-storage.test")
        if operation == "request":
            response = client.post("/api/v1/verification/documents/upload-url/", {
                "doc_type": "CIN", "file_name": "cin.jpg", "content_type": "image/jpeg", "size_bytes": len(JPEG),
            }, format="json")
        elif operation == "confirm":
            response = client.post(f"/api/v1/verification/documents/{document.id}/confirm-upload/")
        else:
            response = client.get(f"/api/v1/verification/documents/{document.id}/download-url/")
    assert response.status_code == 503
    assert "storage is unavailable" in str(response.data)
    document.refresh_from_db()
    assert document.uploaded_at is None
    assert VerificationDocument.objects.count() == 1


@pytest.mark.django_db
def test_changed_upload_cannot_be_snapshotted_or_confirmed():
    user = User.objects.create_user(phone_number="+21620000028")
    document = _document(user)
    original_key = document.storage_key
    client = APIClient()
    client.force_authenticate(user)
    with patch("apps.verification.storage.client") as s3:
        _mock_jpeg(s3.return_value)
        s3.return_value.copy_object.side_effect = ClientError({"Error": {"Code": "PreconditionFailed"}}, "CopyObject")
        response = client.post(f"/api/v1/verification/documents/{document.id}/confirm-upload/")
    assert response.status_code == 400
    document.refresh_from_db()
    assert document.uploaded_at is None and document.storage_key == original_key


@pytest.mark.django_db
def test_placeholder_credentials_do_not_create_pending_document(monkeypatch):
    user = User.objects.create_user(phone_number="+21620000029")
    client = APIClient()
    client.force_authenticate(user)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "dev-placeholder")
    with patch("apps.verification.storage.boto3.client") as storage:
        response = client.post("/api/v1/verification/documents/upload-url/", {
            "doc_type": "CIN", "file_name": "cin.jpg", "content_type": "image/jpeg", "size_bytes": len(JPEG),
        }, format="json")
    assert response.status_code == 503
    assert "upload_url" not in response.data
    assert not VerificationDocument.objects.filter(user=user).exists()
    storage.assert_not_called()
