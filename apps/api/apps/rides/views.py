from django.contrib.gis.geos import Point
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.users.summaries import completed_trips
from apps.wallet.services import InsufficientFunds
from core.schema import schema_or_anonymous

from . import services
from .filters import RideSearchFilter
from .models import Ride
from .selectors import open_rides_near
from .serializers import RideCancellationQuoteSerializer, RideSerializer

UPCOMING = [Ride.Status.OPEN, Ride.Status.FULL]
RIDE_PREFETCH = ("driver__profile__interests", "preferred_interests", "sponsorships__campaign__brand")
SCOPE_PARAM = OpenApiParameter(
    "scope", OpenApiTypes.STR, enum=["bookable", "mine", "joined"],
    description="bookable (default): open future rides; mine: rides you drive; joined: rides you booked",
)


class RideViewSet(
    mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = RideSerializer
    filterset_class = RideSearchFilter
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        if schema_or_anonymous(self):
            return Ride.objects.none()
        user = self.request.user
        rides = (
            Ride.objects.select_related("driver__profile", "vehicle")
            .prefetch_related(*RIDE_PREFETCH)
            .annotate(driver_trips=completed_trips("driver"))
        )
        if self.action == "list":
            scope = self.request.query_params.get("scope", "bookable")
            if scope == "mine":
                return rides.filter(driver=user).order_by("-departure_at", "id")
            if scope == "joined":
                return rides.filter(bookings__passenger=user).distinct().order_by("-departure_at", "id")
            return (
                rides.filter(status=Ride.Status.OPEN, departure_at__gt=timezone.now())
                .exclude(driver=user)
                .order_by("departure_at", "id")
            )
        # Upcoming rides are public to signed-in users; history stays with its driver and passengers.
        return rides.filter(
            Q(status__in=UPCOMING) | Q(driver=user) | Q(bookings__passenger=user)
        ).distinct()

    @extend_schema(parameters=[SCOPE_PARAM])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        try:
            serializer.instance = services.publish_ride(driver=self.request.user, **serializer.validated_data)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def partial_update(self, request, *args, **kwargs):
        ride = self.get_object()
        serializer = self.get_serializer(ride, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            ride = services.update_ride(ride=ride, actor=request.user, **serializer.validated_data)
        except services.NotRideOwner as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except services.RideError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(ride).data)

    def _lifecycle(self, request, operation):
        ride = self.get_object()
        try:
            ride = operation(ride=ride, actor=request.user)
        except services.NotRideOwner as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except InsufficientFunds as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(ride).data)

    @extend_schema(request=None, responses={200: RideSerializer})
    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._lifecycle(request, services.start_ride)

    @extend_schema(request=None, responses={200: RideSerializer})
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return self._lifecycle(request, services.complete_ride)

    @extend_schema(request=None, responses={200: RideSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._lifecycle(request, services.cancel_ride)

    @extend_schema(responses={200: RideCancellationQuoteSerializer})
    @action(detail=True, methods=["get"], url_path="cancellation-quote")
    def cancellation_quote(self, request, pk=None):
        ride = self.get_object()
        try:
            quote = services.cancellation_quote(ride=ride, actor=request.user)
        except services.NotRideOwner as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(RideCancellationQuoteSerializer(quote).data)

    @action(detail=False, methods=["get"], url_path="near")
    def near(self, request):
        """Search open rides whose origin is within radius_km of (lat, lng)."""
        try:
            lat = float(request.query_params["lat"])
            lng = float(request.query_params["lng"])
        except (KeyError, ValueError):
            return Response({"detail": "Numeric 'lat' and 'lng' query params are required"}, status=400)
        radius_km = float(request.query_params.get("radius_km", 5))

        rides = open_rides_near(origin=Point(lng, lat, srid=4326), radius_km=radius_km).prefetch_related(*RIDE_PREFETCH)
        page = self.paginate_queryset(rides)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(self.get_serializer(rides, many=True).data)
