from django.db import transaction

from core.events import publish

from .models import Interest


@transaction.atomic
def create_interest(*, passenger, **fields) -> Interest:
    if fields["latest_departure"] < fields["earliest_departure"]:
        raise ValueError("The departure window ends before it starts")
    if fields["seats"] < 1:
        raise ValueError("At least one seat is required")

    interest = Interest.objects.create(passenger=passenger, **fields)
    publish("interest.created", interest_id=str(interest.id))
    return interest


def cancel_interest(*, interest: Interest, actor) -> Interest:
    if interest.passenger_id != actor.id:
        raise PermissionError("Only the interest's owner can cancel it")
    interest.is_active = False
    interest.save(update_fields=["is_active", "updated_at"])
    return interest
