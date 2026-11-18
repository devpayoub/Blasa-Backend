from celery import shared_task

from core.events import publish

from .scoring import score_interest_ride
from .selectors import matching_interests_for_ride, matching_rides_for_interest

# Matching is an async side-effect of publishing; transient Redis/DB hiccups
# should be retried with exponential backoff instead of dropping matches.
RETRY_POLICY = {
    "autoretry_for": (Exception,),
    "retry_kwargs": {"max_retries": 3},
    "retry_backoff": True,
    "retry_backoff_max": 60,
    "retry_jitter": True,
}


@shared_task(**RETRY_POLICY)
def match_ride_to_interests(*, ride_id) -> int:
    """Run after a ride is published or its route/time/seats change: matching interests fire 'match.found'."""
    from django.utils import timezone

    from apps.rides.models import Ride

    ride = Ride.objects.get(pk=ride_id)
    if ride.status != Ride.Status.OPEN or ride.departure_at <= timezone.now():
        return 0
    count = 0
    for interest in matching_interests_for_ride(ride):
        score = score_interest_ride(interest=interest, ride=ride)
        if score is None:
            continue
        publish(
            "match.found",
            ride_id=str(ride.id),
            interest_id=str(interest.id),
            passenger_id=interest.passenger_id,
            driver_id=ride.driver_id,
            score=score,
        )
        count += 1
    return count


@shared_task(**RETRY_POLICY)
def match_interest_to_rides(*, interest_id) -> int:
    """Run after an interest is registered: every matching ride fires a 'match.found' event."""
    from apps.interests.models import Interest

    interest = Interest.objects.get(pk=interest_id)
    count = 0
    for ride in matching_rides_for_interest(interest):
        score = score_interest_ride(interest=interest, ride=ride)
        if score is None:
            continue
        publish(
            "match.found",
            ride_id=str(ride.id),
            interest_id=str(interest.id),
            passenger_id=interest.passenger_id,
            driver_id=ride.driver_id,
            score=score,
        )
        count += 1
    return count
