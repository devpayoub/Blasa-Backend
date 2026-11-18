from rest_framework import serializers
from rest_framework_gis.fields import GeometryField

from .models import Interest


class InterestSerializer(serializers.ModelSerializer):
    origin = GeometryField()
    destination = GeometryField()

    class Meta:
        model = Interest
        fields = [
            "id", "origin", "destination", "origin_label", "destination_label",
            "earliest_departure", "latest_departure", "seats", "is_active",
        ]
        read_only_fields = ["id", "is_active"]
