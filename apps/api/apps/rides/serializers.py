from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework_gis.fields import GeometryField

from apps.users.models import InterestTag
from apps.users.summaries import UserSummarySerializer, summary_of
from apps.vehicles.models import Vehicle

from .models import Ride


class VehicleSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ["make", "model", "color", "seats"]


class SponsorSummarySerializer(serializers.Serializer):
    """Labeled sponsored content. Aggregate only: no member data reaches the brand."""

    campaign = serializers.UUIDField()
    brand_name = serializers.CharField()
    discount_per_seat = serializers.DecimalField(max_digits=10, decimal_places=3)
    label = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    url = serializers.CharField()
    active = serializers.BooleanField()


class RideSerializer(serializers.ModelSerializer):
    origin = GeometryField()
    destination = GeometryField()
    vehicle = serializers.PrimaryKeyRelatedField(queryset=Vehicle.objects.all())
    preferred_interests = serializers.SlugRelatedField(
        many=True, slug_field="slug", queryset=InterestTag.objects.filter(is_active=True), required=False,
        help_text="Interest slugs the driver would like to share the ride with",
    )
    distance_km = serializers.SerializerMethodField()
    driver_summary = serializers.SerializerMethodField()
    vehicle_summary = VehicleSummarySerializer(source="vehicle", read_only=True)
    sponsorship = serializers.SerializerMethodField()

    class Meta:
        model = Ride
        fields = [
            "id", "driver", "vehicle", "origin", "destination", "origin_label", "destination_label",
            "departure_at", "seats_total", "seats_available", "price_per_seat", "notes", "atmosphere",
            "preferred_interests", "status", "started_at", "completed_at", "cancelled_at", "distance_km",
            "driver_summary", "vehicle_summary", "sponsorship",
        ]
        read_only_fields = [
            "id", "driver", "seats_available", "status", "started_at", "completed_at", "cancelled_at", "distance_km",
        ]

    def get_distance_km(self, obj) -> float | None:
        distance = getattr(obj, "distance", None)
        return round(distance.km, 2) if distance is not None else None

    @extend_schema_field(UserSummarySerializer)
    def get_driver_summary(self, obj):
        return UserSummarySerializer(summary_of(obj.driver, getattr(obj, "driver_trips", None), self.context)).data

    @extend_schema_field(SponsorSummarySerializer(allow_null=True))
    def get_sponsorship(self, obj):
        """Prefetched sponsorships keep the ride list to one extra query per page."""
        from apps.brands.services import sponsor_summary_for_ride

        sponsorships = getattr(obj, "sponsorships", None)
        if sponsorships is not None and not sponsorships:
            return None
        summary = sponsor_summary_for_ride(obj.pk)
        return SponsorSummarySerializer(summary).data if summary else None


class RideRefundLineSerializer(serializers.Serializer):
    booking = serializers.UUIDField()
    status = serializers.CharField()
    refund = serializers.DecimalField(max_digits=10, decimal_places=3)
    subsidy_returned = serializers.DecimalField(max_digits=10, decimal_places=3)
    voucher_returned = serializers.DecimalField(max_digits=10, decimal_places=3)
    seats_released = serializers.IntegerField()


class RideCancellationQuoteSerializer(serializers.Serializer):
    allowed = serializers.BooleanField()
    status = serializers.CharField()
    bookings_cancelled = serializers.IntegerField()
    paid_bookings = serializers.IntegerField()
    total_refund = serializers.DecimalField(max_digits=10, decimal_places=3)
    bookings = RideRefundLineSerializer(many=True)
