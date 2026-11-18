from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.utils import timezone

from apps.interests.models import Interest
from apps.rides.models import Ride

from .scoring import MAX_DEVIATION_KM


def matching_rides_for_interest(interest):
    """Open rides that pass the hard filters for an interest, nearest pickup first."""
    return (
        Ride.objects.filter(
            status=Ride.Status.OPEN,
            departure_at__gt=timezone.now(),
            departure_at__gte=interest.earliest_departure,
            departure_at__lte=interest.latest_departure,
            seats_available__gte=interest.seats,
            origin__distance_lte=(interest.origin, D(km=MAX_DEVIATION_KM)),
            destination__distance_lte=(interest.destination, D(km=MAX_DEVIATION_KM)),
        )
        .select_related("driver__profile", "vehicle")
        .prefetch_related("driver__profile__interests", "preferred_interests")
        .annotate(pickup_deviation=Distance("origin", interest.origin))
        .order_by("pickup_deviation", "departure_at")
    )


def matching_interests_for_ride(ride):
    """Active interests that pass the hard filters for a ride, nearest pickup first."""
    return (
        Interest.objects.filter(
            is_active=True,
            earliest_departure__lte=ride.departure_at,
            latest_departure__gte=ride.departure_at,
            seats__lte=ride.seats_available,
            origin__distance_lte=(ride.origin, D(km=MAX_DEVIATION_KM)),
            destination__distance_lte=(ride.destination, D(km=MAX_DEVIATION_KM)),
        )
        .annotate(pickup_deviation=Distance("origin", ride.origin))
        .order_by("pickup_deviation", "earliest_departure")
    )
