from pathlib import PurePosixPath
from uuid import uuid4

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.schema import schema_or_anonymous

from . import services
from .models import VerificationDocument
from .serializers import (
    DownloadResponseSerializer,
    ErrorSerializer,
    ReviewRequestSerializer,
    RevokeRequestSerializer,
    UploadRequestSerializer,
    UploadResponseSerializer,
    VerificationDocumentSerializer,
)
from .storage import signed_download_url, signed_upload_url


class IsOwnerOrStaff(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user.is_staff or obj.user_id == request.user.id


class VerificationDocumentViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Documents are served through short-lived signed URLs only. Raw keys never leave the API."""

    serializer_class = VerificationDocumentSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrStaff]

    def get_queryset(self):
        if schema_or_anonymous(self):
            return VerificationDocument.objects.none()
        user = self.request.user
        qs = VerificationDocument.objects.select_related("user").prefetch_related("history").order_by("-created_at", "id")
        return qs if user.is_staff else qs.filter(user=user)

    @extend_schema(
        request=UploadRequestSerializer,
        responses={201: UploadResponseSerializer, 400: ErrorSerializer, 403: ErrorSerializer, 503: ErrorSerializer},
        description="Declare PDF/JPEG/PNG MIME, matching filename and 1–10485760 byte size. "
                    "PUT with the declared Content-Type. Disabled until legal sign-off.",
    )
    @action(detail=False, methods=["post"], url_path="upload-url")
    def upload_url(self, request):
        services.ensure_uploads_enabled()
        serializer = UploadRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        suffix = PurePosixPath(data["file_name"]).suffix.lower()
        storage_key = f"verification/{request.user.id}/{data['doc_type'].lower()}/{uuid4().hex}{suffix}"
        try:
            upload_url = signed_upload_url(
                storage_key, content_type=data["content_type"], size_bytes=data["size_bytes"],
                user_id=request.user.pk, doc_type=data["doc_type"],
            )
            doc = services.submit_document(
                user=request.user, doc_type=data["doc_type"], storage_key=storage_key,
                content_type=data["content_type"], size_bytes=data["size_bytes"],
            )
        except ValueError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response({"document_id": str(doc.id), "upload_url": upload_url}, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=None,
        responses={200: VerificationDocumentSerializer, 400: ErrorSerializer, 403: ErrorSerializer, 404: ErrorSerializer, 503: ErrorSerializer},
        description="Confirm only after S3 HEAD and bounded file-signature validation. Idempotent; legal gate applies.",
    )
    @action(detail=True, methods=["post"], url_path="confirm-upload")
    def confirm_upload(self, request, pk=None):
        services.ensure_uploads_enabled()
        doc = self.get_object()
        try:
            doc = services.confirm_upload(document=doc, actor=request.user)
        except ValueError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(self.get_serializer(doc).data)

    @extend_schema(
        responses={200: DownloadResponseSerializer, 403: ErrorSerializer, 404: ErrorSerializer, 503: ErrorSerializer},
        description="Owner/staff only; returns a private GET URL valid for 300 seconds.",
    )
    @action(detail=True, methods=["get"], url_path="download-url")
    def download_url(self, request, pk=None):
        doc = self.get_object()
        return Response({"download_url": signed_download_url(doc.storage_key), "expires_in": 300})

    @extend_schema(
        request=ReviewRequestSerializer,
        responses={200: VerificationDocumentSerializer, 400: ErrorSerializer, 403: ErrorSerializer, 404: ErrorSerializer, 503: ErrorSerializer},
        description="Staff only. APPROVE requires confirmed, storage-validated upload. "
                    "REJECT/REQUEST_MORE require a reason. CIN approval supersedes prior approved CINs.",
    )
    @action(detail=True, methods=["post"], url_path="review", permission_classes=[permissions.IsAdminUser])
    def review(self, request, pk=None):
        doc = self.get_object()
        serializer = ReviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            doc = services.review_document(document=doc, reviewer=request.user, **serializer.validated_data)
        except ValueError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(self.get_serializer(doc).data)

    @extend_schema(
        request=RevokeRequestSerializer,
        responses={200: VerificationDocumentSerializer, 400: ErrorSerializer, 403: ErrorSerializer, 404: ErrorSerializer, 503: ErrorSerializer},
        description="Staff only. Revoke an APPROVED document with a reason; append audit and recompute eligibility.",
    )
    @action(detail=True, methods=["post"], url_path="revoke", permission_classes=[permissions.IsAdminUser])
    def revoke(self, request, pk=None):
        doc = self.get_object()
        serializer = RevokeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            doc = services.revoke_document(document=doc, reviewer=request.user, **serializer.validated_data)
        except ValueError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(self.get_serializer(doc).data)
