
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.users.models import InterestTag

from .models import Brand, Campaign, CampaignEvent, SponsoredRide


class BrandSerializer(serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Brand
        fields = ["id", "name", "owner", "website", "logo_key", "created_at"]
        read_only_fields = ["id", "owner", "created_at"]


class CampaignSerializer(serializers.ModelSerializer):
    brand = serializers.PrimaryKeyRelatedField(queryset=Brand.objects.all())
    balance = serializers.SerializerMethodField()
    audience_interests = serializers.SlugRelatedField(
        many=True, slug_field="slug", queryset=InterestTag.objects.filter(is_active=True), required=False,
        help_text="Interest slugs this offer targets. Empty targets every member.",
    )
    brand_name = serializers.CharField(source="brand.name", read_only=True)

    class Meta:
        model = Campaign
        fields = [
            "id", "brand", "brand_name", "name", "discount_per_seat", "budget", "spent", "balance",
            "starts_at", "ends_at", "is_active", "paused_at", "audience_interests",
            "promotion_title", "promotion_body", "promotion_url", "created_at",
        ]
        read_only_fields = ["id", "spent", "balance", "paused_at", "created_at"]

    @extend_schema_field(serializers.DecimalField(max_digits=14, decimal_places=3))
    def get_balance(self, obj) -> str:
        """Money as a fixed-precision string, exactly like every other amount on the wire.

        Returning a bare Decimal would be rendered as a JSON float and silently
        lose millime precision.
        """
        from apps.wallet.services import balance_of

        from .services import campaign_account

        return f"{balance_of(campaign_account(obj)):.3f}"

    def validate_discount_per_seat(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError("The discount per seat must be positive")
        return value

    def validate_budget(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError("The budget must be positive")
        if self.instance is not None and value < self.instance.spent:
            raise serializers.ValidationError("The budget cannot fall below what was already spent")
        return value

    def validate(self, attrs):
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"ends_at": "The end must be after the start"})
        return attrs


class SponsoredRideSerializer(serializers.ModelSerializer):
    campaign = serializers.PrimaryKeyRelatedField(queryset=Campaign.objects.all())
    ride_label = serializers.SerializerMethodField()

    class Meta:
        model = SponsoredRide
        fields = ["id", "campaign", "ride", "ride_label", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_ride_label(self, obj) -> str:
        ride = obj.ride
        return f"{ride.origin_label} → {ride.destination_label}"


class SponsoredRideCreateSerializer(serializers.Serializer):
    ride = serializers.UUIDField()


class CampaignAnalyticsSerializer(serializers.Serializer):
    campaign = serializers.UUIDField()
    impressions = serializers.IntegerField()
    engagements = serializers.IntegerField()
    redemptions = serializers.IntegerField()
    promo_codes_issued = serializers.IntegerField()
    subsidy_spend = serializers.DecimalField(max_digits=14, decimal_places=3)
    promo_redemption_value = serializers.DecimalField(max_digits=14, decimal_places=3)
    promo_value_outstanding = serializers.DecimalField(max_digits=14, decimal_places=3)
    budget = serializers.DecimalField(max_digits=14, decimal_places=3)
    budget_remaining = serializers.DecimalField(max_digits=14, decimal_places=3)
    balance = serializers.DecimalField(max_digits=14, decimal_places=3)
    audience_tags = serializers.IntegerField()


class CampaignEventInputSerializer(serializers.Serializer):
    """Client-reported visibility only. The dedupe key and the ride come from the URL."""

    kind = serializers.ChoiceField(choices=[CampaignEvent.Kind.IMPRESSION, CampaignEvent.Kind.ENGAGEMENT])


class PromoCodeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=3)
    expires_at = serializers.DateTimeField(allow_null=True)
    redeemed_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class PromoCodeCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=3)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class FundingIntentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=3)


class FundingResultSerializer(serializers.Serializer):
    intent = serializers.UUIDField()
    status = serializers.CharField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=3)
    payment_url = serializers.URLField(allow_null=True, required=False)
