from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.users.models import User

from . import services
from .models import IncentiveAward, IncentiveCampaign, Voucher
from .services import issue_voucher


class VoucherSerializer(serializers.ModelSerializer):
    class Meta:
        model = Voucher
        fields = ["id", "code", "amount", "expires_at", "redeemed_at", "created_at"]


class VoucherIssueSerializer(serializers.Serializer):
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=10, decimal_places=3)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def create(self, validated_data):
        return issue_voucher(
            user=validated_data.get("user"), amount=validated_data["amount"], expires_at=validated_data.get("expires_at")
        )


def is_expired(voucher: Voucher) -> bool:
    return voucher.expires_at is not None and voucher.expires_at < timezone.now()


class VoucherClaimSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=40)


class IncentiveCampaignSerializer(serializers.ModelSerializer):
    balance = serializers.SerializerMethodField()
    awards_issued = serializers.SerializerMethodField()

    class Meta:
        model = IncentiveCampaign
        fields = [
            "id", "name", "kind", "amount", "is_active", "starts_at", "ends_at", "award_validity_days",
            "balance", "awards_issued", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_balance(self, obj) -> str:
        return str(services.campaign_balance(obj))

    def get_awards_issued(self, obj) -> int:
        return obj.awards.count()

    def validate(self, attrs):
        amount = attrs.get("amount", getattr(self.instance, "amount", None))
        if amount is not None and amount <= 0:
            raise serializers.ValidationError({"amount": "Reward amount must be positive."})
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"ends_at": "The campaign must end after it starts."})
        if self.instance is not None and "kind" in attrs and attrs["kind"] != self.instance.kind:
            raise serializers.ValidationError({"kind": "A campaign's kind cannot change."})
        return attrs


class CampaignFundSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"))
    idempotency_key = serializers.CharField(max_length=60, required=False, allow_blank=True)


class FuelRedeemSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=40)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)


class IncentiveAwardSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()
    campaign_name = serializers.CharField(source="campaign.name", read_only=True)

    class Meta:
        model = IncentiveAward
        fields = ["id", "kind", "campaign_name", "amount", "status", "code", "expires_at", "redeemed_at", "created_at"]

    def get_status(self, obj) -> str:
        return services.award_status(obj)
