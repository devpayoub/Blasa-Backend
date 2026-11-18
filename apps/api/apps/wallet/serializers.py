from rest_framework import serializers

from .models import LedgerEntry


class LedgerEntrySerializer(serializers.ModelSerializer):
    """Read-only view of the user's ledger history."""

    kind = serializers.CharField(source="transaction.kind", read_only=True)
    reference = serializers.CharField(source="transaction.reference", read_only=True)
    description = serializers.CharField(source="transaction.description", read_only=True)
    booking = serializers.SerializerMethodField()

    class Meta:
        model = LedgerEntry
        fields = ["id", "kind", "reference", "description", "booking", "amount", "created_at"]

    def get_booking(self, obj) -> str | None:
        prefix, _, suffix = obj.transaction.reference.partition(":")
        return suffix if prefix in BOOKING_REFERENCE_PREFIXES else None


# Ledger references whose suffix is a booking id (bookings, payments, brands and incentives services).
BOOKING_REFERENCE_PREFIXES = frozenset({
    "ride_payment", "refund", "voucher_redeem", "voucher_redeem_reversal",
    "sponsor_subsidy", "sponsor_subsidy_reversal",
})
