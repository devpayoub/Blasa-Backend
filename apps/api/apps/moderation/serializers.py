from rest_framework import serializers

from .models import Ban, Report


class ReportSerializer(serializers.ModelSerializer):
    reporter = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Report
        fields = [
            "id", "reporter", "target_type", "target_id", "reason", "details",
            "status", "handled_by", "handled_at", "created_at",
        ]
        read_only_fields = ["id", "reporter", "status", "handled_by", "handled_at", "created_at"]


class BanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ban
        fields = ["id", "user", "reason", "issued_by", "revoked_at", "created_at"]
        read_only_fields = ["id", "issued_by", "revoked_at", "created_at"]


class AuditEntrySerializer(serializers.Serializer):
    """A staff decision, safe to return to staff: actor identity, object and decision only."""

    id = serializers.UUIDField()
    action = serializers.CharField()
    object_type = serializers.CharField()
    object_id = serializers.CharField()
    reason = serializers.CharField()
    metadata = serializers.JSONField()
    created_at = serializers.DateTimeField()
    actor_display_name = serializers.SerializerMethodField()

    def get_actor_display_name(self, obj) -> str:
        from apps.users.summaries import display_name

        return display_name(obj.actor) if obj.actor else "System"


class RideOverrideInputSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["cancel", "complete"])
    reason = serializers.CharField(required=False, allow_blank=True, max_length=200)


class RideOverrideResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
