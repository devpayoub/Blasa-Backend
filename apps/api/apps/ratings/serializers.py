from rest_framework import serializers

from .models import Rating


class RatingSerializer(serializers.ModelSerializer):
    rater = serializers.PrimaryKeyRelatedField(read_only=True)
    ratee = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Rating
        fields = ["id", "booking", "rater", "ratee", "score", "comment", "created_at"]
        read_only_fields = ["id", "booking", "rater", "ratee", "created_at"]
