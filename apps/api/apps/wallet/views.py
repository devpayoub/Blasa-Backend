from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.payments.services import driver_earnings
from core.schema import schema_or_anonymous

from .models import LedgerEntry
from .serializers import LedgerEntrySerializer
from .services import balance_of, ensure_user_account


class WalletView(APIView):
    """The authenticated user's wallet balance."""

    def get(self, request):
        account = ensure_user_account(request.user)
        return Response({"balance": balance_of(account), "currency": account.currency})


class LedgerHistoryView(ListAPIView):
    """The authenticated user's ledger entries, newest first. Immutable, read-only."""

    serializer_class = LedgerEntrySerializer

    def get_queryset(self):
        if schema_or_anonymous(self):
            return LedgerEntry.objects.none()
        account = ensure_user_account(self.request.user)
        entries = account.entries.select_related("transaction").order_by("-created_at", "-pk")
        kinds = [k for k in self.request.query_params.get("kind", "").upper().split(",") if k]
        if kinds:
            entries = entries.filter(transaction__kind__in=kinds)
        return entries


class EarningsView(APIView):
    """A driver's ride income from the ledger: fares paid, the platform fee, and refunds."""

    def get(self, request):
        if request.user.role != request.user.Role.DRIVER:
            return Response({"detail": "Earnings are available to drivers only."}, status=status.HTTP_403_FORBIDDEN)
        return Response(driver_earnings(request.user))
