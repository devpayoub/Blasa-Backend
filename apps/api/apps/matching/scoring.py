import math

from apps.rides.models import Ride

# Component weights of the match score.
W_PICKUP = 0.4    # how close the ride's origin is to the passenger's pickup point
W_DETOUR = 0.3    # how much the ride's destination deviates from the passenger's dropoff
W_TIME = 0.3      # how well the departure sits in the passenger's time window

# Matches beyond this endpoint deviation are dropped by the selectors.
MAX_DEVIATION_KM = 15.0

EARTH_RADIUS_KM = 6371.0088


def haversine_km(a, b) -> float:
    """Great-circle distance in km between two GEOS points (lon/lat, WGS84)."""
    lon1, lat1 = math.radians(a.x), math.radians(a.y)
    lon2, lat2 = math.radians(b.x), math.radians(b.y)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def _deviation_score(km: float) -> float:
    """1 at zero deviation, decaying linearly to 0 at MAX_DEVIATION_KM."""
    return max(0.0, 1.0 - km / MAX_DEVIATION_KM)


def _time_score(departure, earliest, latest) -> float:
    """1 at the middle of the window, decaying to 0 at its edges."""
    if latest <= earliest:
        return 1.0 if departure == earliest else 0.0
    half = (latest - earliest) / 2
    mid = earliest + half
    offset = abs((departure - mid).total_seconds())
    return max(0.0, 1.0 - offset / half.total_seconds())


def score_interest_ride(*, interest, ride) -> float | None:
    """Match score 0..100 for a (interest, ride) pair, or None when the pair cannot match."""
    if ride.status != Ride.Status.OPEN:
        return None
    if ride.seats_available < interest.seats:
        return None
    if not (interest.earliest_departure <= ride.departure_at <= interest.latest_departure):
        return None

    pickup_km = haversine_km(ride.origin, interest.origin)
    dropoff_km = haversine_km(ride.destination, interest.destination)
    total = (
        W_PICKUP * _deviation_score(pickup_km)
        + W_DETOUR * _deviation_score(dropoff_km)
        + W_TIME * _time_score(ride.departure_at, interest.earliest_departure, interest.latest_departure)
    )
    return round(100 * total, 1)
