from rest_framework import serializers

from apps.verification.models import VerificationDocument

from .models import Vehicle


class VehicleSerializer(serializers.ModelSerializer):
    carte_grise = serializers.PrimaryKeyRelatedField(
        queryset=VerificationDocument.objects.filter(doc_type="CARTE_GRISE"),
        required=False,
        allow_null=True,
    )
    is_verified = serializers.ReadOnlyField()
    is_bookable = serializers.ReadOnlyField()

    class Meta:
        model = Vehicle
        fields = [
            "id", "make", "model", "color", "plate_number", "seats",
            "carte_grise", "is_active", "is_verified", "is_bookable",
        ]
        read_only_fields = ["id"]
