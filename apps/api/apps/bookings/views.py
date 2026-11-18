from django.db import IntegrityError
from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.users.summaries import completed_trips
from apps.wallet.services import InsufficientFunds
from core.schema import schema_or_anonymous

from . import services
from .models import Booking
from .serializers import BookingSerializer, CancellationQuoteSerializer, PaymentQuoteSerializer, TransitionSerializer
from .services import BookingError
from .state_machine import InvalidTransition


def _error(exc, code):
    return Response({"detail": str(exc)}, status=code)


class BookingViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = BookingSerializer
    filterset_fields = ["ride"]

    def get_queryset(self):
        if schema_or_anonymous(self):
            return Booking.objects.none()
        user = self.request.user
        return (
            Booking.objects.filter(Q(passenger=user) | Q(ride__driver=user))
            .select_related("passenger__profile")
            .prefetch_related("passenger__profile__interests")
            .annotate(passenger_trips=completed_trips("passenger"))
            .order_by("-created_at", "id")
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            booking = services.create_booking(passenger=request.user, **serializer.validated_data)
        except BookingError as exc:
            return _error(exc, status.HTTP_400_BAD_REQUEST)
        except IntegrityError:
            # Database-level guard (e.g. booking_seats_positive) fired first
            return Response({"detail": "Invalid booking"}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(booking).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=TransitionSerializer, responses={200: BookingSerializer})
    @action(detail=True, methods=["post"], url_path=r"transitions/(?P<name>[a-z_]+)")
    def transition(self, request, pk=None, name=None):
        booking = self.get_object()
        body = TransitionSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            booking = services.apply_action(
                booking=booking, action=name, actor=request.user,
                expected_out_of_pocket=body.validated_data.get("expected_out_of_pocket"),
            )
        except services.ActionNotAllowed as exc:
            return _error(exc, status.HTTP_403_FORBIDDEN)
        except (InvalidTransition, InsufficientFunds, BookingError, ValueError) as exc:
            # Invalid state, unpaid balance, a changed total or seats taken between request and accept
            return _error(exc, status.HTTP_409_CONFLICT)
        except IntegrityError:
            # Seat check constraint caught a race that slipped past the row lock
            return Response({"detail": "Seats were taken by someone else"}, status=status.HTTP_409_CONFLICT)
        return Response(BookingSerializer(booking).data)

    @extend_schema(responses={200: PaymentQuoteSerializer})
    @action(detail=True, methods=["get"], url_path="payment-quote")
    def payment_quote(self, request, pk=None):
        try:
            quote = services.payment_quote(booking=self.get_object(), actor=request.user)
        except services.ActionNotAllowed as exc:
            return _error(exc, status.HTTP_403_FORBIDDEN)
        except InvalidTransition as exc:
            return _error(exc, status.HTTP_409_CONFLICT)
        return Response(PaymentQuoteSerializer(quote).data)

    @extend_schema(responses={200: CancellationQuoteSerializer})
    @action(detail=True, methods=["get"], url_path="cancellation-quote")
    def cancellation_quote(self, request, pk=None):
        try:
            quote = services.cancellation_quote(booking=self.get_object(), actor=request.user)
        except services.ActionNotAllowed as exc:
            return _error(exc, status.HTTP_403_FORBIDDEN)
        return Response(CancellationQuoteSerializer(quote).data)
