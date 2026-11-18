from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.schema import schema_or_anonymous

from . import services
from .models import IncentiveAward, IncentiveCampaign, Voucher
from .serializers import (
    CampaignFundSerializer,
    FuelRedeemSerializer,
    IncentiveAwardSerializer,
    IncentiveCampaignSerializer,
    VoucherClaimSerializer,
    VoucherIssueSerializer,
    VoucherSerializer,
)
from .services import IncentiveError, VoucherError


class VoucherViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """GET: the caller's live vouchers. POST (staff): issue a voucher. POST claim/: redeem an open code."""

    serializer_class = VoucherSerializer

    def get_queryset(self):
        if schema_or_anonymous(self):
            return Voucher.objects.none()
        return services.available_for(self.request.user)

    def get_permissions(self):
        if self.action == "create":
            return [IsAdminUser()]
        return [IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        serializer = VoucherIssueSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            voucher = serializer.save()
        except VoucherError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(VoucherSerializer(voucher).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], serializer_class=VoucherClaimSerializer)
    def claim(self, request):
        serializer = VoucherClaimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            voucher = services.claim_voucher(user=request.user, code=serializer.validated_data["code"])
        except VoucherError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(VoucherSerializer(voucher).data, status=status.HTTP_201_CREATED)


class IncentiveCampaignViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Staff-only management of welcome-credit and fuel-reward campaigns."""

    serializer_class = IncentiveCampaignSerializer
    permission_classes = [IsAdminUser]
    queryset = IncentiveCampaign.objects.all()

    @action(detail=True, methods=["post"], serializer_class=CampaignFundSerializer)
    def fund(self, request, pk=None):
        campaign = self.get_object()
        serializer = CampaignFundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        balance = services.fund_incentive_campaign(
            campaign=campaign, amount=serializer.validated_data["amount"],
            reference=serializer.validated_data.get("idempotency_key", ""),
        )
        return Response({"balance": str(balance)})

    @action(detail=True, methods=["post"])
    def evaluate(self, request, pk=None):
        try:
            awarded = services.evaluate_campaign(self.get_object())
        except IncentiveError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"awarded": awarded})


class RewardListView(generics.ListAPIView):
    """The caller's welcome credit and fuel rewards."""

    serializer_class = IncentiveAwardSerializer

    def get_queryset(self):
        if schema_or_anonymous(self):
            return IncentiveAward.objects.none()
        return IncentiveAward.objects.filter(user=self.request.user).select_related("campaign", "voucher")


class FuelRedeemView(APIView):
    """Staff record a fuel reward honoured at a partner station (manual until a partner is contracted)."""

    permission_classes = [IsAdminUser]

    def post(self, request):
        serializer = FuelRedeemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            award = services.redeem_fuel_award(
                code=serializer.validated_data["code"], staff=request.user,
                note=serializer.validated_data.get("note", ""),
            )
        except IncentiveError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(IncentiveAwardSerializer(award).data)
