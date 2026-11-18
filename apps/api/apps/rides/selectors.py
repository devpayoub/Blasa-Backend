from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.utils import timezone

from .models import Ride


def open_rides_near(*, origin, radius_km: float = 5):
    """Open rides whose origin is within radius_km of the passenger's pickup point."""
    return (
        Ride.objects.filter(
            status=Ride.Status.OPEN,
            departure_at__gt=timezone.now(),
            origin__distance_lte=(origin, D(km=radius_km)),
        )
        .annotate(distance=Distance("origin", origin))
        .order_by("departure_at", "distance")
    )
