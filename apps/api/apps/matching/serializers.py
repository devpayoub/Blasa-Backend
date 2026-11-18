from rest_framework import serializers

from apps.interests.serializers import InterestSerializer
from apps.rides.serializers import RideSerializer


class ScoredRideSerializer(RideSerializer):
    """Ride output plus the match score (0..100) computed against an interest."""

    score = serializers.SerializerMethodField()

    def get_score(self, obj) -> float:
        return getattr(obj, "score", None)

    class Meta(RideSerializer.Meta):
        fields = [*RideSerializer.Meta.fields, "score"]


class ScoredInterestSerializer(InterestSerializer):
    """Interest output plus the match score (0..100) computed against a ride."""

    score = serializers.SerializerMethodField()

    def get_score(self, obj) -> float:
        return getattr(obj, "score", None)

    class Meta(InterestSerializer.Meta):
        fields = [*InterestSerializer.Meta.fields, "score"]
