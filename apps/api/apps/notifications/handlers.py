from .services import notify


def on_booking_status_changed(*, booking_id, status, event_id=None, **_):
    from apps.bookings.models import Booking

    booking = Booking.objects.select_related("passenger", "ride__driver").get(pk=booking_id)
    channels = ("in_app", "push", "email", "sms") if status == "CONFIRMED" else ("in_app", "push", "email")
    for user in (booking.passenger, booking.ride.driver):
        notify(
            user=user, kind="booking.status_changed", status=status,
            event_key=f"booking:{event_id or f'{booking_id}:{status}'}",
            payload={"booking_id": str(booking_id), "refund": str(booking.refunded_amount or "0.000")},
            channels=channels,
        )


def on_booking_created(*, booking_id, ride_id, **_):
    from apps.bookings.models import Booking

    booking = Booking.objects.select_related("ride__driver").get(pk=booking_id)
    notify(
        user=booking.ride.driver, kind="booking.created", event_key=f"booking.created:{booking_id}",
        payload={"booking_id": str(booking_id), "ride_id": str(ride_id)},
    )


def on_ride_published(*, ride_id, **_):
    from apps.rides.models import Ride

    ride = Ride.objects.select_related("driver").get(pk=ride_id)
    notify(user=ride.driver, kind="ride.published", event_key=f"ride.published:{ride_id}",
           payload={"ride_id": str(ride_id)}, channels=("in_app",))


def on_ride_status_changed(*, ride_id, status, **_):
    if status != "CANCELLED":
        return
    from apps.rides.models import Ride

    ride = Ride.objects.select_related("driver").get(pk=ride_id)
    notify(user=ride.driver, kind="ride.cancelled", event_key=f"ride.cancelled:{ride_id}",
           payload={"ride_id": str(ride_id)})


def on_match_found(*, interest_id, ride_id, passenger_id, **_):
    from apps.users.models import User

    passenger = User.objects.get(pk=passenger_id)
    notify(
        user=passenger, kind="match.found", event_key=f"match:{interest_id}:{ride_id}",
        payload={"interest_id": str(interest_id), "ride_id": str(ride_id)},
    )


def on_verification_changed(*, document_id, status, review_id=None, **_):
    if status not in {"APPROVED", "REJECTED", "NEEDS_MORE_DOCUMENTS"}:
        return
    from apps.verification.models import VerificationDocument

    document = VerificationDocument.objects.select_related("user").get(pk=document_id)
    notify(
        user=document.user, kind="verification.changed", status=status,
        event_key=f"verification:{review_id or f'{document_id}:{status}'}",
        payload={"document_id": str(document_id), "reason": document.rejection_reason[:160]},
        channels=("in_app", "push", "email"),
    )


def on_payment_status_changed(*, intent_id, status, **_):
    from apps.payments.models import PaymentIntent

    intent = PaymentIntent.objects.select_related("user").get(pk=intent_id)
    notify(user=intent.user, kind="payment.status_changed", status=status,
           event_key=f"payment:{intent_id}:{status}", payload={"intent_id": str(intent_id), "amount": str(intent.amount)},
           channels=("in_app", "push", "email"))


def on_wallet_posted(*, transaction_id, **_):
    from apps.wallet.models import LedgerEntry

    for entry in LedgerEntry.objects.filter(transaction_id=transaction_id, account__owner__isnull=False).select_related(
        "account__owner", "transaction",
    ):
        notify(
            user=entry.account.owner, kind="wallet.posted", event_key=f"wallet:{entry.pk}",
            payload={"entry_id": str(entry.pk), "amount": str(entry.amount), "reference": entry.transaction.reference,
                     "description": entry.transaction.kind.replace("_", " ").lower()},
            channels=("in_app",),
        )


def on_incentive_awarded(*, award_id, **_):
    from apps.incentives.models import IncentiveAward

    award = IncentiveAward.objects.select_related("user").get(pk=award_id)
    notify(
        user=award.user, kind="incentive.awarded", event_key=f"award:{award.pk}",
        payload={"award_id": str(award.pk), "amount": str(award.amount), "expires_at": award.expires_at.date().isoformat()},
        promotional=True,
    )
