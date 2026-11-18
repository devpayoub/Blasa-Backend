from django.db import IntegrityError, transaction
from django.db.models import Avg

from apps.bookings.models import Booking
from apps.bookings.state_machine import BookingStatus

from .models import Rating


class RatingError(ValueError):
    """Domain rule violation; the view layer maps this to HTTP 400."""


@transaction.atomic
def rate_booking(*, booking: Booking, rater, score: int, comment: str = "") -> Rating:
    if not 1 <= int(score) <= 5:
        raise RatingError("Score must be between 1 and 5")
    if booking.status != BookingStatus.COMPLETED:
        raise RatingError("Only completed rides can be rated")
    if rater.id == booking.passenger_id:
        ratee = booking.ride.driver
    elif rater.id == booking.ride.driver_id:
        ratee = booking.passenger
    else:
        raise RatingError("Only the booking's participants can rate each other")

    try:
        rating = Rating.objects.create(booking=booking, rater=rater, ratee=ratee, score=score, comment=comment)
    except IntegrityError:
        raise RatingError("You already rated this ride") from None

    update_average_rating(user=ratee)
    return rating


def update_average_rating(*, user) -> None:
    from apps.users.models import Profile

    profile, _ = Profile.objects.get_or_create(user=user, defaults={"full_name": str(user)})
    average = Rating.objects.filter(ratee=user).aggregate(average=Avg("score"))["average"]
    profile.average_rating = round(average, 2) if average is not None else 0
    profile.save(update_fields=["average_rating", "updated_at"])
