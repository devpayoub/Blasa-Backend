from decimal import Decimal, InvalidOperation

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.incentives.models import Voucher
from apps.rides.models import Ride
from apps.rides.serializers import SponsorSummarySerializer
from apps.wallet.services import InsufficientFunds

from . import services
from .models import Brand, Campaign, SponsoredRide
from .serializers import (
    BrandSerializer,
    CampaignAnalyticsSerializer,
    CampaignEventInputSerializer,
    CampaignSerializer,
    FundingIntentSerializer,
    FundingResultSerializer,
    PromoCodeCreateSerializer,
    PromoCodeSerializer,
    SponsoredRideCreateSerializer,
    SponsoredRideSerializer,
)
from .services import CampaignError, NotBrandOwner


class IsBrandOrStaff(BasePermission):
    """Brand accounts operate their own brand; staff keep full access."""

    message = "A brand account is required"

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated
            and (user.is_staff or user.role == user.Role.BRAND)
        )


class OwnedQuerysetMixin:
    """Never trust a path id: every lookup is re-scoped to the caller's brands."""

    def get_queryset(self):
        queryset = services.owned_campaigns(self.request.user).select_related("brand").prefetch_related(
            "audience_interests"
        )
        if self.request.query_params.get("status") == "paused":
            queryset = queryset.filter(is_active=False)
        elif self.request.query_params.get("status") == "active":
            queryset = queryset.filter(is_active=True)
        return queryset


class BrandViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    """The caller's brand. Creation stays staff-only; owners may edit contact details."""

    serializer_class = BrandSerializer
    permission_classes = [IsBrandOrStaff]

    def get_queryset(self):
        return services.owned_brands(self.request.user)

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAdminUser()]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save(owner=serializer.validated_data.get("owner") or self.request.user)

    @action(detail=False, methods=["get"], url_path="mine")
    def mine(self, request):
        brand = services.brand_of(request.user)
        if brand is None:
            return Response({"detail": "No brand is linked to this account"}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(brand).data)


class CampaignViewSet(
    OwnedQuerysetMixin, viewsets.GenericViewSet,
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin,
):
    """Campaign management for the owning brand, plus funding, promo and analytics actions."""

    serializer_class = CampaignSerializer
    permission_classes = [IsBrandOrStaff]

    def _owned(self, pk):
        """Resolve inside the caller's scope. Another brand's id is simply not found."""
        campaign = self.get_queryset().filter(pk=pk).first()
        if campaign is None:
            raise NotFound("Campaign not found")
        return campaign

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        brand = serializer.validated_data.get("brand")
        try:
            services.assert_brand_owner(brand, request.user)
        except NotBrandOwner as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        instance = self._owned(kwargs["pk"])
        serializer = self.get_serializer(instance, data=request.data, partial=kwargs.pop("partial", False))
        serializer.is_valid(raise_exception=True)
        # A campaign can never move to another brand through the portal.
        if "brand" in request.data and str(request.data["brand"]) != str(instance.brand_id):
            return Response({"detail": "A campaign cannot move to another brand"}, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        return Response(self.get_serializer(self._owned(kwargs["pk"])).data)

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        campaign = self._owned(pk)
        campaign.is_active = False
        campaign.paused_at = timezone.now()
        campaign.save(update_fields=["is_active", "paused_at", "updated_at"])
        return Response(self.get_serializer(campaign).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        campaign = self._owned(pk)
        campaign.is_active = True
        campaign.paused_at = None
        campaign.save(update_fields=["is_active", "paused_at", "updated_at"])
        return Response(self.get_serializer(campaign).data)

    @extend_schema(request=FundingIntentSerializer, responses={201: FundingResultSerializer})
    @action(detail=True, methods=["post"], url_path="funding-intents")
    def funding_intents(self, request, pk=None):
        campaign = self._owned(pk)
        serializer = FundingIntentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            intent, data = services.create_funding_intent(
                campaign=campaign, user=request.user, amount=serializer.validated_data["amount"],
            )
        except CampaignError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "intent": str(intent.pk), "status": intent.status, "amount": intent.amount,
                "payment_url": data.get("payment_url") if isinstance(data, dict) else None,
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(request=FundingIntentSerializer, responses={201: dict})
    @action(detail=True, methods=["post"], url_path="fund")
    def fund(self, request, pk=None):
        """Staff-reconciled transfer. Brand accounts never reach this path."""
        if not request.user.is_staff:
            return Response({"detail": "Staff only"}, status=status.HTTP_403_FORBIDDEN)
        campaign = self._owned(pk)
        try:
            amount = Decimal(str(request.data.get("amount")))
        except (TypeError, ValueError, InvalidOperation):
            return Response({"detail": "A positive amount is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            account = services.fund_campaign(campaign=campaign, amount=amount)
        except CampaignError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except InsufficientFunds as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"account": account.code, "amount": str(amount)}, status=status.HTTP_201_CREATED)

    @extend_schema(responses=CampaignAnalyticsSerializer)
    @action(detail=True, methods=["get"])
    def analytics(self, request, pk=None):
        campaign = self._owned(pk)
        return Response(CampaignAnalyticsSerializer(services.campaign_analytics(campaign)).data)

    @extend_schema(request=SponsoredRideCreateSerializer, responses=SponsoredRideSerializer(many=True))
    @action(detail=True, methods=["get", "post"], url_path="sponsored-rides")
    def sponsored_rides(self, request, pk=None):
        """GET: rides this campaign sponsors. POST: attach one (same brand only)."""
        campaign = self._owned(pk)
        if request.method == "GET":
            return Response(SponsoredRideSerializer(services.campaign_rides(campaign), many=True).data)
        serializer = SponsoredRideCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ride = Ride.objects.filter(pk=serializer.validated_data["ride"]).first()
        if ride is None:
            return Response({"detail": "Unknown ride"}, status=status.HTTP_400_BAD_REQUEST)
        if not campaign.covers():
            return Response({"detail": "Campaign is not active"}, status=status.HTTP_400_BAD_REQUEST)
        if ride.driver_id == campaign.brand.owner_id:
            return Response({"detail": "A brand cannot sponsor its own rides"}, status=status.HTTP_400_BAD_REQUEST)
        sponsorship, created = SponsoredRide.objects.get_or_create(campaign=campaign, ride=ride)
        return Response(
            SponsoredRideSerializer(sponsorship).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        request=None,
        responses={204: None},
        # The nested ride id is not the campaign pk, so name it explicitly for the schema.
        parameters=[OpenApiParameter("ride_id", OpenApiTypes.UUID, OpenApiParameter.PATH)],
    )
    @action(detail=True, methods=["delete"], url_path=r"sponsored-rides/(?P<ride_id>[^/.]+)")
    def remove_sponsored_ride(self, request, pk=None, ride_id=None):
        campaign = self._owned(pk)
        deleted, _ = SponsoredRide.objects.filter(campaign=campaign, ride_id=ride_id).delete()
        if not deleted:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=PromoCodeCreateSerializer, responses=PromoCodeSerializer(many=True))
    @action(detail=True, methods=["get", "post"], url_path="promo-codes")
    def promo_codes(self, request, pk=None):
        """GET: codes issued by this campaign. POST: issue one within the remaining budget."""
        campaign = self._owned(pk)
        if request.method == "GET":
            vouchers = Voucher.objects.filter(campaign=campaign).order_by("-created_at")
            return Response(PromoCodeSerializer(vouchers, many=True).data)
        serializer = PromoCodeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            voucher = services.issue_promo_code(
                campaign=campaign, amount=serializer.validated_data["amount"],
                expires_at=serializer.validated_data.get("expires_at"), actor=request.user,
            )
        except CampaignError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PromoCodeSerializer(voucher).data, status=status.HTTP_201_CREATED)



class SponsoredRideViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Staff-only surface for attaching a ride to a campaign."""

    queryset = SponsoredRide.objects.all()
    serializer_class = SponsoredRideSerializer
    permission_classes = [IsAdminUser]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        campaign = serializer.validated_data["campaign"]
        ride = serializer.validated_data["ride"]
        if not campaign.covers():
            return Response({"detail": "Campaign is not active"}, status=status.HTTP_400_BAD_REQUEST)
        if ride.driver_id == campaign.brand.owner_id:
            return Response({"detail": "A brand cannot sponsor its own rides"}, status=status.HTTP_400_BAD_REQUEST)
        sponsorship = serializer.save()
        return Response(SponsoredRideSerializer(sponsorship).data, status=status.HTTP_201_CREATED)


class CampaignPublicView(APIView):
    """Passenger-facing sponsored content for one ride: labeled, aggregate only."""

    authentication_classes = []
    permission_classes = []

    @extend_schema(responses={200: SponsorSummarySerializer, 404: None})
    def get(self, request, ride_id):
        summary = services.sponsor_summary_for_ride(ride_id)
        if summary is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(summary)


class CampaignEventView(APIView):
    """Impression/engagement reported by the member who saw the sponsored card.

    The dedupe key is derived on the server (campaign, ride, member, kind, day),
    so a client cannot inflate counts by replaying. Financial outcomes are never
    accepted here: redemptions come from the ledger-backed voucher flow.
    """

    @extend_schema(request=CampaignEventInputSerializer, responses={202: dict})
    def post(self, request, ride_id):
        serializer = CampaignEventInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        campaign = services.sponsorship_of(ride_id)
        if campaign is None:
            return Response({"detail": "This ride is not sponsored"}, status=status.HTTP_404_NOT_FOUND)
        kind = serializer.validated_data["kind"]
        day = timezone.now().date().isoformat()
        event = services.record_event(
            campaign=campaign, kind=kind,
            dedupe_key=f"{kind}:{request.user.pk}:{ride_id}:{campaign.pk}:{day}",
            ride=Ride.objects.filter(pk=ride_id).first(), user=request.user,
            metadata={"source": "client"},
        )
        return Response({"recorded": event is not None}, status=status.HTTP_202_ACCEPTED)


__all__ = [
    "BrandViewSet", "CampaignViewSet", "SponsoredRideViewSet", "CampaignPublicView", "IsBrandOrStaff",
    "Brand", "Campaign",
]
