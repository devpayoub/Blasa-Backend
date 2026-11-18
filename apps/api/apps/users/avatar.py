from pathlib import PurePosixPath
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core import signing
from django.db import transaction

from apps.verification.storage import StorageUnavailable, client, signed_download_url, snapshot_upload

from .models import Profile

MAX_BYTES = 5 * 1024 * 1024
SALT = "blasa.avatar-upload"
TYPES = {"image/jpeg": {"jpg", "jpeg"}, "image/png": {"png"}}


def request_upload(*, user, file_name, content_type, size_bytes):
    suffix = PurePosixPath(file_name).suffix.lower().lstrip(".")
    if content_type not in TYPES or suffix not in TYPES[content_type]:
        raise ValueError("Choose a JPEG or PNG image with a matching file extension")
    if not 0 < size_bytes <= MAX_BYTES:
        raise ValueError("Profile photos must be between 1 byte and 5 MB")
    key = f"avatars/{user.id}/{uuid4().hex}.{suffix}"
    try:
        url = client().generate_presigned_url(
            "put_object", Params={"Bucket": settings.S3_BUCKET_PRIVATE, "Key": key, "ContentType": content_type},
            ExpiresIn=600,
        )
    except BotoCoreError as exc:
        raise StorageUnavailable() from exc
    token = signing.dumps({"user": str(user.id), "key": key, "type": content_type, "size": size_bytes}, salt=SALT)
    return {"upload_url": url, "upload_token": token}


def confirm_upload(*, user, upload_token):
    try:
        payload = signing.loads(upload_token, salt=SALT, max_age=900)
    except signing.BadSignature as exc:
        raise ValueError("Upload confirmation is invalid or expired. Choose the photo again.") from exc
    if payload["user"] != str(user.id) or not payload["key"].startswith(f"avatars/{user.id}/"):
        raise ValueError("Upload confirmation does not belong to this account")
    try:
        storage = client()
        object_args = {"Bucket": settings.S3_BUCKET_PRIVATE, "Key": payload["key"]}
        metadata = storage.head_object(**object_args)
        if metadata["ContentLength"] != payload["size"] or metadata["ContentType"] != payload["type"]:
            raise ValueError("Uploaded photo size or type does not match the selected file")
        etag = metadata["ETag"]
        response = storage.get_object(**object_args, Range="bytes=0-15", IfMatch=etag)
        stream = response["Body"]
        try:
            if response["ETag"] != etag or response["ContentType"] != payload["type"]:
                raise ValueError("The uploaded photo changed during confirmation")
            prefix = stream.read(16)
        finally:
            stream.close()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            raise ValueError("The photo has not finished uploading") from exc
        raise StorageUnavailable() from exc
    except BotoCoreError as exc:
        raise StorageUnavailable() from exc
    expected = b"\xff\xd8\xff" if payload["type"] == "image/jpeg" else b"\x89PNG\r\n\x1a\n"
    if not prefix.startswith(expected):
        raise ValueError("The uploaded file is not a supported image")
    key = snapshot_upload(storage, payload["key"], etag)
    with transaction.atomic():
        profile, _ = Profile.objects.get_or_create(user=user, defaults={"full_name": ""})
        profile = Profile.objects.select_for_update().get(pk=profile.pk)
        profile.avatar_key = key
        profile.save(update_fields=["avatar_key", "updated_at"])
    return user


def download_url(user):
    profile = Profile.objects.filter(user=user).first()
    if not profile or not profile.avatar_key:
        raise ValueError("No profile photo has been uploaded")
    try:
        return {"download_url": signed_download_url(profile.avatar_key), "expires_in": 300}
    except BotoCoreError as exc:
        raise StorageUnavailable() from exc
