from rest_framework import serializers

from .models import VerificationDocument, VerificationReview
from .storage import ALLOWED_CONTENT_TYPES, MAX_UPLOAD_BYTES, validate_upload_metadata


class VerificationReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationReview
        fields = ["actor", "decision", "reason", "timestamp"]
        read_only_fields = fields


class VerificationDocumentSerializer(serializers.ModelSerializer):
    history = VerificationReviewSerializer(many=True, read_only=True)

    class Meta:
        model = VerificationDocument
        fields = [
            "id", "doc_type", "content_type", "size_bytes", "status", "uploaded_at",
            "reviewed_at", "rejection_reason", "created_at", "history",
        ]
        read_only_fields = fields


class UploadRequestSerializer(serializers.Serializer):
    doc_type = serializers.ChoiceField(choices=VerificationDocument.DocType.choices)
    file_name = serializers.CharField(max_length=255)
    content_type = serializers.ChoiceField(choices=list(ALLOWED_CONTENT_TYPES))
    size_bytes = serializers.IntegerField(min_value=1, max_value=MAX_UPLOAD_BYTES)

    def validate(self, attrs):
        try:
            validate_upload_metadata(
                content_type=attrs["content_type"], size_bytes=attrs["size_bytes"], file_name=attrs["file_name"],
            )
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return attrs


class UploadResponseSerializer(serializers.Serializer):
    document_id = serializers.UUIDField()
    upload_url = serializers.URLField()


class DownloadResponseSerializer(serializers.Serializer):
    download_url = serializers.URLField()
    expires_in = serializers.IntegerField()


class ReviewRequestSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["APPROVE", "REJECT", "REQUEST_MORE"])
    reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=4000)

    def validate(self, attrs):
        if attrs["decision"] != "APPROVE" and not attrs["reason"].strip():
            raise serializers.ValidationError({"reason": "A reason is required for this decision"})
        return attrs


class RevokeRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=4000, allow_blank=False)


class ErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
