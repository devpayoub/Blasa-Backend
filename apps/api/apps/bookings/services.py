from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.brands import services as brands_services
from apps.incentives import services as incentives_services
from apps.messaging import services as messaging_services
from apps.payments import services as payment_services
from apps.rides import services as ride_services
from apps.rides.models import Ride
from apps.wallet.services import balance_of, ensure_user_account
from core.events import publish_on_commit

from .models import Booking, BookingEvent
from .state_machine import TERMINAL, TRANSITIONS, BookingStatus, InvalidTransition, next_status

ZERO = Decimal("0.000")

# Actions that take seats out of the ride inventory.
RESERVE_ACTION = "accept"
# Actions that give seats back — only when seats were actually held (not from REQUESTED).
RELEASE_ACTIONS = {"reject", "cancel_passenger", "cancel_driver"}
STATUSES_WITH_HELD_SEATS = {BookingStatus.ACCEPTED, BookingStatus.CONFIRMED}
ACTIVE_STATUSES = {BookingStatus.REQUESTED, BookingStatus.ACCEPTED, BookingStatus.CONFIRMED}

# The driver runs the trip; the passenger owns the booking and authorizes its payment.
DRIVER_ACTIONS = {"accept", "reject", "complete", "no_show", "cancel_driver"}
PASSENGER_ACTIONS = {"confirm", "cancel_passenger"}

UPCOMING = {Ride.Status.OPEN, Ride.Status.FULL}
RIDE_STATES_FOR_ACTION = {
    "accept": {Ride.Status.OPEN},
    "reject": UPCOMING,
    "confirm": UPCOMING,
    "cancel_passenger": UPCOMING,
    "cancel_driver": UPCOMING,
    "complete": {Ride.Status.IN_PROGRESS},
    "no_show": {Ride.Status.IN_PROGRESS},
}


class ActionNotAllowed(PermissionError):
    """The actor may not perform this action; the view layer maps this to HTTP 403."""


class BookingError(ValueError):
    """Domain rule violation; the view layer maps this to HTTP 400/409."""


class QuoteChanged(BookingError):
    """The amount the passenger approved no longer matches the server amount; nothing was charged."""

    def __init__(self):
        super().__init__("The amount to pay changed. Review the new total before confirming.")


def _check_actor(*, booking: Booking, action: str, actor) -> None:
    if action in DRIVER_ACTIONS and booking.ride.driver_id != actor.id:
        raise ActionNotAllowed("Only the ride's driver can perform this action")
    if action in PASSENGER_ACTIONS and booking.passenger_id != actor.id:
        raise ActionNotAllowed("Only the booking's passenger can perform this action")


def _check_ride_state(*, ride: Ride, action: str) -> None:
    allowed = RIDE_STATES_FOR_ACTION.get(action)
    if allowed is not None and ride.status not in allowed:
        raise InvalidTransition(f"Action '{action}' not allowed while the ride is {ride.status}")


def _lock(booking_pk) -> tuple[Ride, Booking]:
    # Ride first, then booking: the same order as ride-wide cancel/complete, so
    # concurrent booking and ride actions can never deadlock.
    ride_pk = Booking.objects.values_list("ride_id", flat=True).get(pk=booking_pk)
    ride = Ride.objects.select_for_update().get(pk=ride_pk)
    booking = (
        Booking.objects.select_for_update(of=("self",))
        .select_related("ride", "ride__driver", "passenger")
        .get(pk=booking_pk)
    )
    return ride, booking


@transaction.atomic
def create_booking(*, passenger, ride: Ride, seats: int, pickup_point, dropoff_point) -> Booking:
    ride = Ride.objects.select_for_update().get(pk=ride.pk)
    if ride.status != Ride.Status.OPEN:
        raise BookingError("Ride is not open for booking")
    if ride.departure_at <= timezone.now():
        raise BookingError("This ride has already departed")
    if ride.driver_id == passenger.id:
        raise BookingError("A driver cannot book their own ride")
    if seats < 1:
        raise BookingError("At least one seat is required")
    if seats > ride.seats_available:
        raise BookingError("Not enough seats available")
    if Booking.objects.filter(ride=ride, passenger=passenger, status__in=ACTIVE_STATUSES).exists():
        raise BookingError("You already have a booking on this ride")

    booking = Booking.objects.create(
        ride=ride,
        passenger=passenger,
        seats=seats,
        pickup_point=pickup_point,
        dropoff_point=dropoff_point,
        fare_amount=ride.price_per_seat * seats,  # Server-side fare math, never client-supplied
    )
    # Every booking gets its own driver↔passenger chat thread.
    messaging_services.get_or_create_conversation(booking)
    publish_on_commit("booking.created", booking_id=str(booking.id), ride_id=str(ride.id))
    return booking


def _quote(booking: Booking) -> dict:
    fare = booking.fare_amount
    subsidy = brands_services.quote_subsidy(booking)
    voucher, voucher_amount = incentives_services.quote_voucher(booking, cap=fare - subsidy)
    out_of_pocket = fare - subsidy - voucher_amount
    balance = balance_of(ensure_user_account(booking.passenger))
    return {
        "fare": fare,
        "subsidy": subsidy,
        "voucher": voucher_amount,
        "voucher_code": voucher.code if voucher is not None and voucher_amount > 0 else None,
        "out_of_pocket": out_of_pocket,
        "balance": balance,
        "sufficient": balance >= out_of_pocket,
    }


def payment_quote(*, booking: Booking, actor) -> dict:
    """What confirming this booking would charge the passenger, computed server-side."""
    if booking.passenger_id != actor.id:
        raise ActionNotAllowed("Only the booking's passenger can see its payment")
    if booking.status != BookingStatus.ACCEPTED:
        raise InvalidTransition(f"Booking is {booking.status}, not awaiting payment")
    return _quote(booking)


def refund_breakdown(booking: Booking) -> dict:
    """What cancelling now returns. Paid bookings are reversed in full before the ride starts."""
    paid = booking.status == BookingStatus.CONFIRMED
    return {
        "refund": (booking.paid_amount or ZERO) if paid else ZERO,
        "subsidy_returned": (booking.subsidy_amount or ZERO) if paid else ZERO,
        "voucher_returned": (booking.voucher_amount or ZERO) if paid else ZERO,
        "seats_released": booking.seats if booking.status in STATUSES_WITH_HELD_SEATS else 0,
    }


def cancellation_quote(*, booking: Booking, actor) -> dict:
    is_driver = booking.ride.driver_id == actor.id
    if not is_driver and booking.passenger_id != actor.id:
        raise ActionNotAllowed("Only the ride's driver or the booking's passenger can cancel")
    action = "cancel_driver" if is_driver else "cancel_passenger"
    allowed = (
        booking.status not in TERMINAL
        and (booking.status, action) in TRANSITIONS
        and booking.ride.status in RIDE_STATES_FOR_ACTION[action]
    )
    return {"action": action, "allowed": allowed, "status": booking.status, **refund_breakdown(booking)}


def _settle(booking: Booking, expected_out_of_pocket) -> None:
    if expected_out_of_pocket is None:
        raise BookingError("expected_out_of_pocket is required to confirm a payment")
    try:
        expected = Decimal(str(expected_out_of_pocket)).quantize(Decimal("0.001"))
    except (InvalidOperation, ValueError):
        raise BookingError("expected_out_of_pocket must be a decimal amount") from None

    # Discounts post before the fare settles, so the passenger only needs cash
    # for the remainder: sponsor subsidy first, then a voucher capped at what is left.
    fare = booking.fare_amount
    subsidy = brands_services.subsidy_for_booking(booking) or ZERO
    voucher = incentives_services.redeem_voucher_for_booking(booking, cap=fare - subsidy) or ZERO
    out_of_pocket = fare - subsidy - voucher
    if out_of_pocket != expected:
        # Raising rolls back the discounts above along with the transition.
        raise QuoteChanged()
    payment_services.settle_booking_payment(booking=booking, fare=fare)
    booking.subsidy_amount, booking.voucher_amount, booking.paid_amount = subsidy, voucher, out_of_pocket


def _refund(booking: Booking) -> None:
    returned = payment_services.refund_booking_payment(booking=booking)
    brands_services.reverse_subsidy_for_booking(booking)
    incentives_services.reverse_voucher_for_booking(booking)
    # The passenger's net refund is their own cash: the fare minus discounts returned to their funders.
    booking.refunded_amount = returned - (booking.subsidy_amount or ZERO) - (booking.voucher_amount or ZERO)


def _apply_locked(
    *, ride: Ride, booking: Booking, action: str, actor, expected_out_of_pocket=None, staff_override: bool = False,
) -> Booking:
    previous = booking.status
    new_status = next_status(previous, action)
    if not staff_override:
        _check_actor(booking=booking, action=action, actor=actor)
    _check_ride_state(ride=ride, action=action)

    if action == RESERVE_ACTION:
        ride_services.reserve_seats(ride_id=ride.pk, seats=booking.seats)
    elif action in RELEASE_ACTIONS and previous in STATUSES_WITH_HELD_SEATS:
        ride_services.release_seats(ride_id=ride.pk, seats=booking.seats)
        if previous == BookingStatus.CONFIRMED:
            # Ledger legs share this transaction: a failed refund cancels the transition too.
            _refund(booking)

    if action == "confirm":
        _settle(booking, expected_out_of_pocket)

    booking.status = new_status
    booking.save(update_fields=[
        "status", "subsidy_amount", "voucher_amount", "paid_amount", "refunded_amount", "updated_at",
    ])
    event = BookingEvent.objects.create(
        booking=booking, from_status=previous, to_status=new_status, action=action, actor=actor
    )
    publish_on_commit("booking.status_changed", booking_id=str(booking.id), status=new_status, event_id=str(event.pk))
    return booking


@transaction.atomic
def apply_action(*, booking: Booking, action: str, actor, expected_out_of_pocket=None) -> Booking:
    ride, booking = _lock(booking.pk)
    return _apply_locked(
        ride=ride, booking=booking, action=action, actor=actor, expected_out_of_pocket=expected_out_of_pocket
    )


def active_bookings_for_update(ride: Ride):
    """Lock the ride's bookings in a stable order; callers already hold the ride lock."""
    return list(
        Booking.objects.select_for_update(of=("self",))
        .select_related("ride", "ride__driver", "passenger")
        .filter(ride=ride, status__in=ACTIVE_STATUSES)
        .order_by("pk")
    )
