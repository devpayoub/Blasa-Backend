from django.shortcuts import get_object_or_404
from rest_framework.generics import ListAPIView

from apps.interests.models import Interest
from apps.rides.models import Ride
from core.schema import schema_or_anonymous

from . import scoring, selectors
from .serializers import ScoredInterestSerializer, ScoredRideSerializer


def _interest_slugs(user) -> set[str]:
    profile = getattr(user, "profile", None)
    return set(profile.interests.values_list("slug", flat=True)) if profile else set()


def rank_key(score: float, shared: int, departure, pk):
    """Whole route-score points rank first; shared interests only break ties between equally good routes."""
    return (-int(score), -shared, departure, str(pk))


class RidesForInterestView(ListAPIView):
    """Ranked open rides for one of the caller's interests, best route first."""

    serializer_class = ScoredRideSerializer

    def get_queryset(self):
        if schema_or_anonymous(self):
            return Ride.objects.none()
        interest = get_object_or_404(Interest, pk=self.kwargs["interest_id"], passenger=self.request.user)
        mine = _interest_slugs(self.request.user)
        scored = []
        for ride in selectors.matching_rides_for_interest(interest):
            score = scoring.score_interest_ride(interest=interest, ride=ride)
            if score is not None:
                ride.score = score
                shared = sum(tag.slug in mine for tag in ride.driver.profile.interests.all())
                scored.append((rank_key(score, shared, ride.departure_at, ride.pk), ride))
        return [ride for _, ride in sorted(scored, key=lambda pair: pair[0])]


class InterestsForRideView(ListAPIView):
    """Ranked active interests for one of the caller's (driver's) rides, best route first."""

    serializer_class = ScoredInterestSerializer

    def get_queryset(self):
        if schema_or_anonymous(self):
            return Interest.objects.none()
        ride = get_object_or_404(Ride, pk=self.kwargs["ride_id"], driver=self.request.user)
        mine = _interest_slugs(self.request.user)
        scored = []
        for interest in selectors.matching_interests_for_ride(ride).prefetch_related("passenger__profile__interests"):
            score = scoring.score_interest_ride(interest=interest, ride=ride)
            if score is not None:
                interest.score = score
                shared = sum(tag.slug in mine for tag in interest.passenger.profile.interests.all())
                scored.append((rank_key(score, shared, interest.earliest_departure, interest.pk), interest))
        return [interest for _, interest in sorted(scored, key=lambda pair: pair[0])]
