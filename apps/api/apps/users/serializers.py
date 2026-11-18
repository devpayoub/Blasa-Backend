from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import InterestTag, Profile, User


class InterestTagSerializer(serializers.ModelSerializer):
    labels = serializers.SerializerMethodField()

    class Meta:
        model = InterestTag
        fields = ["slug", "labels"]

    @extend_schema_field({"type": "object", "properties": {k: {"type": "string"} for k in ("en", "fr", "ar")}})
    def get_labels(self, obj):
        return {"en": obj.label_en, "fr": obj.label_fr, "ar": obj.label_ar}


class ProfileSerializer(serializers.ModelSerializer):
    interests = serializers.SlugRelatedField(
        many=True, slug_field="slug", queryset=InterestTag.objects.filter(is_active=True), required=False,
    )

    class Meta:
        model = Profile
        fields = ["full_name", "avatar_key", "bio", "average_rating", "interests"]
        read_only_fields = ["avatar_key", "average_rating"]


def _write_profile(profile: Profile, data: dict) -> None:
    interests = data.pop("interests", None)
    for field, value in data.items():
        setattr(profile, field, value)
    profile.save()
    if interests is not None:
        profile.interests.set(interests)


class UserPublicReviewSerializer(serializers.Serializer):
    score = serializers.IntegerField()
    comment = serializers.CharField()
    created_at = serializers.DateTimeField()
    rater_display_name = serializers.CharField()


class UserPublicProfileSerializer(serializers.Serializer):
    """Participant-facing profile. Deliberately has no contact, document or wallet fields."""

    id = serializers.UUIDField()
    display_name = serializers.CharField()
    bio = serializers.CharField()
    member_since = serializers.DateTimeField()
    is_driver = serializers.BooleanField()
    average_rating = serializers.DecimalField(max_digits=3, decimal_places=2)
    review_count = serializers.IntegerField()
    completed_trips = serializers.IntegerField()
    shared_interests = serializers.ListField(child=serializers.SlugField())
    reviews_visible = serializers.BooleanField()
    reviews = UserPublicReviewSerializer(many=True)
    is_self = serializers.BooleanField()


class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(required=False)
    onboarding_complete = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "phone_number",
            "email",
            "phone_verified_at",
            "email_verified_at",
            "role",
            "preferred_language",
            "is_verified",
            "profile",
            "onboarding_complete",
        ]
        read_only_fields = [
            "id",
            "phone_number",
            "email",
            "phone_verified_at",
            "email_verified_at",
            "role",
            "is_verified",
        ]

    @extend_schema_field(serializers.BooleanField)
    def get_onboarding_complete(self, obj):
        try:
            return bool(obj.profile.full_name.strip())
        except Profile.DoesNotExist:
            return False

    @transaction.atomic
    def update(self, instance, validated_data):
        # Serialize nested writes even for legacy accounts with no Profile row.
        instance = User.objects.select_for_update().get(pk=instance.pk)
        profile_data = validated_data.pop("profile", None)
        instance = super().update(instance, validated_data)
        if profile_data is not None:
            profile, _ = Profile.objects.get_or_create(user=instance)
            _write_profile(profile, profile_data)
        return instance

    @transaction.atomic
    def create(self, validated_data):
        profile_data = validated_data.pop("profile", {})
        user = User.objects.create_user(**validated_data)
        _write_profile(user.profile, profile_data)
        return user
