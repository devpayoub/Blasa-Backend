from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.bookings.models import Booking
from core.schema import schema_or_anonymous

from . import services
from .models import Rating
from .serializers import RatingSerializer
from .services import RatingError


class RatingViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """POST to rate the other party of a completed booking; GET lists ratings received."""

    serializer_class = RatingSerializer

    def get_queryset(self):
        if schema_or_anonymous(self):
            return Rating.objects.none()
        # Stable ordering: pagination must not shuffle reviews between pages.
        return Rating.objects.filter(ratee=self.request.user).select_related("booking", "rater").order_by("-created_at", "-id")

    def create(self, request, *args, **kwargs):
        booking_id = request.data.get("booking")
        booking = Booking.objects.filter(pk=booking_id).first()
        if booking is None:
            return Response({"detail": "Unknown booking"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            score = int(request.data.get("score"))
        except (TypeError, ValueError):
            return Response({"detail": "A score from 1 to 5 is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            rating = services.rate_booking(
                booking=booking, rater=request.user, score=score,
                comment=request.data.get("comment", ""),
            )
        except RatingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(RatingSerializer(rating).data, status=status.HTTP_201_CREATED)
