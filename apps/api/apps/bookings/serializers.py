from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework_gis.fields import GeometryField

from apps.users.summaries import UserSummarySerializer, summary_of

from .models import Booking


class BookingSerializer(serializers.ModelSerializer):
    pickup_point = GeometryField()
    dropoff_point = GeometryField()
    passenger_summary = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            "id", "ride", "passenger", "seats", "pickup_point", "dropoff_point", "status", "fare_amount",
            "subsidy_amount", "voucher_amount", "paid_amount", "refunded_amount", "passenger_summary",
        ]
        read_only_fields = [
            "id", "passenger", "status", "fare_amount", "subsidy_amount", "voucher_amount", "paid_amount",
            "refunded_amount",
        ]

    @extend_schema_field(UserSummarySerializer)
    def get_passenger_summary(self, obj):
        return UserSummarySerializer(
            summary_of(obj.passenger, getattr(obj, "passenger_trips", None), self.context)
        ).data


class TransitionSerializer(serializers.Serializer):
    expected_out_of_pocket = serializers.DecimalField(
        max_digits=10, decimal_places=3, required=False,
        help_text="Required for confirm: the out_of_pocket the passenger approved from payment-quote",
    )


class PaymentQuoteSerializer(serializers.Serializer):
    fare = serializers.DecimalField(max_digits=10, decimal_places=3)
    subsidy = serializers.DecimalField(max_digits=10, decimal_places=3)
    voucher = serializers.DecimalField(max_digits=10, decimal_places=3)
    voucher_code = serializers.CharField(allow_null=True)
    out_of_pocket = serializers.DecimalField(max_digits=10, decimal_places=3)
    balance = serializers.DecimalField(max_digits=12, decimal_places=3)
    sufficient = serializers.BooleanField()


class CancellationQuoteSerializer(serializers.Serializer):
    action = serializers.CharField()
    allowed = serializers.BooleanField()
    status = serializers.CharField()
    refund = serializers.DecimalField(max_digits=10, decimal_places=3)
    subsidy_returned = serializers.DecimalField(max_digits=10, decimal_places=3)
    voucher_returned = serializers.DecimalField(max_digits=10, decimal_places=3)
    seats_released = serializers.IntegerField()
