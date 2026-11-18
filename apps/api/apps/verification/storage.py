from os import environ
from pathlib import PurePosixPath
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from rest_framework.exceptions import APIException

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "application/pdf": {".pdf"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
}


class StorageUnavailable(APIException):
    status_code = 503
    default_detail = "File storage is unavailable. Please try again later."


def client():
    if any(environ.get(name) == "dev-placeholder" for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")):
        raise StorageUnavailable()
    try:
        return boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            region_name=getattr(settings, "S3_REGION", None),
        )
    except BotoCoreError as exc:
        raise StorageUnavailable() from exc


def validate_upload_metadata(*, content_type, size_bytes, file_name):
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError("Only PDF, JPEG and PNG documents are accepted")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or not 1 <= size_bytes <= MAX_UPLOAD_BYTES:
        raise ValueError("Document size must be between 1 byte and 10 MiB")
    if PurePosixPath(file_name).suffix.lower() not in ALLOWED_CONTENT_TYPES[content_type]:
        raise ValueError("File extension must match the declared content type")


def validate_storage_key(*, storage_key, user_id, doc_type):
    prefix = f"verification/{user_id}/{doc_type.lower()}/"
    if not storage_key.startswith(prefix):
        raise ValueError("Document storage key must belong to its owner and document type")
    name = storage_key[len(prefix):]
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("Invalid document storage key")


def signed_upload_url(storage_key: str, *, content_type: str, size_bytes: int, user_id, doc_type,
                      expires_in: int = 600) -> str:
    """Sign only an owner's supported upload; the PUT must send the declared Content-Type."""
    validate_storage_key(storage_key=storage_key, user_id=user_id, doc_type=doc_type)
    validate_upload_metadata(content_type=content_type, size_bytes=size_bytes, file_name=storage_key)
    try:
        return client().generate_presigned_url(
            "put_object",
            Params={"Bucket": settings.S3_BUCKET_PRIVATE, "Key": storage_key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )
    except BotoCoreError as exc:
        raise StorageUnavailable() from exc


def validate_stored_document(document):
    """HEAD plus a bounded, conditional Range GET; never trust client confirmation or MIME alone."""
    validate_storage_key(storage_key=document.storage_key, user_id=document.user_id, doc_type=document.doc_type)
    validate_upload_metadata(
        content_type=document.content_type, size_bytes=document.size_bytes, file_name=document.storage_key,
    )
    s3 = client()
    params = {"Bucket": settings.S3_BUCKET_PRIVATE, "Key": document.storage_key}
    try:
        metadata = s3.head_object(**params)
        length = metadata.get("ContentLength")
        if isinstance(length, bool) or not isinstance(length, int) or length != document.size_bytes:
            raise ValueError("Stored document size does not match the declared size")
        if not 1 <= length <= MAX_UPLOAD_BYTES:
            raise ValueError("Stored document exceeds the size limit")
        if metadata.get("ContentType") != document.content_type:
            raise ValueError("Stored document content type does not match the declared type")
        etag = metadata.get("ETag")
        if not isinstance(etag, str) or not etag:
            raise ValueError("Stored document has no object signature")
        response = s3.get_object(**params, Range="bytes=0-15", IfMatch=etag)
        body = response["Body"]
        try:
            magic = body.read(16)
        finally:
            body.close()
        if response.get("ETag") != etag or response.get("ContentType") != document.content_type:
            raise ValueError("Stored document changed during validation")
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound", "InvalidObjectState", "InvalidRange",
                    "PreconditionFailed", "412", "416"}:
            raise ValueError("Uploaded document is missing or invalid") from exc
        raise StorageUnavailable() from exc
    except BotoCoreError as exc:
        raise StorageUnavailable() from exc
    signatures = {"application/pdf": b"%PDF-", "image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
    if not isinstance(magic, bytes) or not magic.startswith(signatures[document.content_type]):
        raise ValueError("Stored document file signature does not match the declared type")
    return s3, etag


def snapshot_upload(storage, storage_key, etag):
    # The confirmed key must not remain writable through the original PUT grant.
    source = PurePosixPath(storage_key)
    key = str(source.with_name(f"confirmed-{uuid4().hex}{source.suffix}"))
    try:
        storage.copy_object(
            Bucket=settings.S3_BUCKET_PRIVATE, Key=key,
            CopySource={"Bucket": settings.S3_BUCKET_PRIVATE, "Key": storage_key},
            CopySourceIfMatch=etag,
        )
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code", "")) in {"412", "PreconditionFailed", "404", "NoSuchKey"}:
            raise ValueError("The uploaded file changed during confirmation. Choose it again.") from exc
        raise StorageUnavailable() from exc
    except BotoCoreError as exc:
        raise StorageUnavailable() from exc
    return key


def signed_download_url(storage_key: str, expires_in: int = 300) -> str:
    """Return a short-lived URL for a private document. Never expose raw keys."""
    try:
        return client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_BUCKET_PRIVATE, "Key": storage_key},
            ExpiresIn=expires_in,
        )
    except BotoCoreError as exc:
        raise StorageUnavailable() from exc
