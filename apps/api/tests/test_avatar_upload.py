import time
from io import BytesIO
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from rest_framework.test import APIClient

from apps.users import avatar
from apps.users.models import Profile, User


@pytest.fixture
def user_client(db):
    user = User.objects.create_user(phone_number="+21620881101")
    Profile.objects.get_or_create(user=user, defaults={"full_name": "Synthetic avatar user"})
    client = APIClient()
    client.force_authenticate(user)
    return user, client


def _request(client):
    with patch("apps.users.avatar.client") as storage:
        storage.return_value.generate_presigned_url.return_value = "https://s3.test/avatar-put"
        response = client.post("/api/v1/users/me/avatar/upload-url/", {
            "file_name": "synthetic.png", "content_type": "image/png", "size_bytes": 16,
        }, format="json")
    assert response.status_code == 201
    return response.data["upload_token"]


@pytest.mark.django_db
def test_avatar_confirm_requires_owned_token_and_stored_image(user_client):
    user, client = user_client
    token = _request(client)
    user.profile.refresh_from_db()
    assert not user.profile.avatar_key
    with patch("apps.users.avatar.client") as storage:
        storage.return_value.head_object.return_value = {
            "ContentLength": 16, "ContentType": "image/png", "ETag": "synthetic-etag",
        }
        storage.return_value.get_object.return_value = {
            "Body": BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * 8), "ETag": "synthetic-etag", "ContentType": "image/png",
        }
        response = client.post("/api/v1/users/me/avatar/confirm-upload/", {"upload_token": token}, format="json")
        copy = storage.return_value.copy_object.call_args.kwargs
    assert response.status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.avatar_key.startswith(f"avatars/{user.id}/")
    assert copy["Key"] == user.profile.avatar_key
    assert copy["CopySource"]["Key"] != user.profile.avatar_key
    assert copy["CopySourceIfMatch"] == "synthetic-etag"
    with patch("apps.users.avatar.signed_download_url", return_value="https://s3.test/avatar-get"):
        assert client.get("/api/v1/users/me/avatar/download-url/").data["download_url"] == "https://s3.test/avatar-get"


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["missing", "size", "mime", "magic", "changed", "tampered", "foreign", "expired"])
def test_avatar_rejects_unverified_files_and_tokens(user_client, failure):
    user, client = user_client
    token = _request(client)
    if failure == "tampered":
        token += "x"
    elif failure == "foreign":
        client.force_authenticate(User.objects.create_user(phone_number="+21620881102"))
    now = time.time()
    with patch("apps.users.avatar.client") as storage, patch("django.core.signing.time.time") as clock:
        clock.return_value = now + (1000 if failure == "expired" else 0)
        if failure == "missing":
            storage.return_value.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        else:
            storage.return_value.head_object.return_value = {
                "ContentLength": 15 if failure == "size" else 16,
                "ContentType": "image/jpeg" if failure == "mime" else "image/png", "ETag": "synthetic-etag",
            }
            storage.return_value.get_object.return_value = {
                "Body": BytesIO(b"not an image" if failure == "magic" else b"\x89PNG\r\n\x1a\n" + b"x" * 8),
                "ETag": "synthetic-etag", "ContentType": "image/png",
            }
            if failure == "changed":
                storage.return_value.copy_object.side_effect = ClientError(
                    {"Error": {"Code": "PreconditionFailed"}}, "CopyObject"
                )
        response = client.post("/api/v1/users/me/avatar/confirm-upload/", {"upload_token": token}, format="json")
    assert response.status_code == 400
    user.profile.refresh_from_db()
    assert not user.profile.avatar_key


@pytest.mark.django_db
@pytest.mark.parametrize("fields", [
    {"file_name": "script.png", "content_type": "text/html", "size_bytes": 12},
    {"file_name": "photo.pdf", "content_type": "image/png", "size_bytes": 12},
    {"file_name": "photo.png", "content_type": "image/png", "size_bytes": avatar.MAX_BYTES + 1},
])
def test_avatar_declaration_rejects_invalid_type_size_and_suffix(user_client, fields):
    _, client = user_client
    response = client.post("/api/v1/users/me/avatar/upload-url/", fields, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_avatar_storage_unavailable_does_not_change_profile(user_client):
    user, client = user_client
    with patch("apps.users.avatar.client", side_effect=NoCredentialsError()):
        response = client.post("/api/v1/users/me/avatar/upload-url/", {
            "file_name": "synthetic.png", "content_type": "image/png", "size_bytes": 16,
        }, format="json")
    assert response.status_code == 503
    user.profile.refresh_from_db()
    assert not user.profile.avatar_key


@pytest.mark.django_db
@pytest.mark.parametrize("credential", ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"])
def test_placeholder_credentials_do_not_issue_avatar_upload_url(user_client, monkeypatch, credential):
    user, client = user_client
    monkeypatch.setenv(credential, "dev-placeholder")
    with patch("apps.verification.storage.boto3.client") as storage:
        response = client.post("/api/v1/users/me/avatar/upload-url/", {
            "file_name": "synthetic.png", "content_type": "image/png", "size_bytes": 16,
        }, format="json")
    assert response.status_code == 503
    assert "upload_url" not in response.data
    storage.assert_not_called()
    user.profile.refresh_from_db()
    assert not user.profile.avatar_key
