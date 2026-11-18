from decimal import Decimal

from rest_framework import serializers

from .models import PaymentIntent


class TopupSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("1.000"))
    gateway = serializers.ChoiceField(choices=PaymentIntent.Gateway.choices)


class PaymentIntentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentIntent
        fields = ["id", "kind", "gateway", "amount", "currency", "status", "gateway_reference", "created_at"]
