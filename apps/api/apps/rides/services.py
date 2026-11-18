from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.events import publish_on_commit

from .models import Ride

UPCOMING = {Ride.Status.OPEN, Ride.Status.FULL}
# Fields a booked passenger agreed to; they freeze once anyone holds or requests a seat.
BOOKED_TERMS = {
    "vehicle": "vehicle", "origin": "pickup point", "destination": "drop-off point", "origin_label": "pickup name",
    "destination_label": "drop-off name", "departure_at": "departure time", "price_per_seat": "price",
}
# Edits that can change which saved route alerts a ride satisfies.
MATCH_TERMS = {"origin", "destination", "departure_at", "seats_total"}


class RideError(ValueError):
    """Domain rule violation; the view layer maps this to HTTP 400/409."""


class NotRideOwner(PermissionError):
    """Only the ride's driver may manage it; the view layer maps this to HTTP 403."""


def _check_vehicle(*, driver, vehicle, seats_total: int) -> None:
    if vehicle.driver_id != driver.id:
        raise RideError("The vehicle must belong to the driver")
    if not vehicle.is_bookable:
        raise RideError("The vehicle must be active with an approved Carte Grise")
    if seats_total > vehicle.seats:
        raise RideError(f"This vehicle offers at most {vehicle.seats} passenger seats")


@transaction.atomic
def publish_ride(*, driver, vehicle, **fields) -> Ride:
    """Publishing requires a driver role and a bookable vehicle (active + approved Carte Grise)."""
    if driver.role != driver.Role.DRIVER:
        raise RideError("Only drivers can publish rides")
    _check_vehicle(driver=driver, vehicle=vehicle, seats_total=fields["seats_total"])
    if fields["departure_at"] <= timezone.now():
        raise RideError("Departure must be in the future")
    preferred = fields.pop("preferred_interests", None)
    ride = Ride.objects.create(
        driver=driver,
        vehicle=vehicle,
        seats_available=fields["seats_total"],
        **fields,
    )
    if preferred:
        ride.preferred_interests.set(preferred)
    publish_on_commit("ride.published", ride_id=str(ride.id))
    return ride


def _lock_owned(ride: Ride, actor) -> Ride:
    ride = Ride.objects.select_for_update(of=("self",)).select_related("vehicle").get(pk=ride.pk)
    if ride.driver_id != actor.id:
        raise NotRideOwner("Only the ride's driver can manage it")
    return ride


@transaction.atomic
def update_ride(*, ride: Ride, actor, **fields) -> Ride:
    """Owner edits. Notes are always editable; agreed terms freeze once bookings exist."""
    from apps.bookings.services import ACTIVE_STATUSES

    ride = _lock_owned(ride, actor)
    if ride.status not in UPCOMING:
        raise RideError(f"A {ride.status.lower().replace('_', ' ')} ride can no longer be edited")
    preferred = fields.pop("preferred_interests", None)
    changed = {name: value for name, value in fields.items() if getattr(ride, name) != value}
    booked = ride.bookings.filter(status__in=ACTIVE_STATUSES).exists()
    frozen = [label for name, label in BOOKED_TERMS.items() if name in changed]
    if booked and frozen:
        raise RideError(f"The {', '.join(frozen)} can't change while bookings are pending or paid")

    if "departure_at" in changed and changed["departure_at"] <= timezone.now():
        raise RideError("Departure must be in the future")
    held = ride.seats_total - ride.seats_available
    seats_total = changed.get("seats_total", ride.seats_total)
    if seats_total < max(held, 1):
        raise RideError(f"{held} seats are already held; seats cannot go below that")
    if "vehicle" in changed or "seats_total" in changed:
        _check_vehicle(driver=actor, vehicle=changed.get("vehicle", ride.vehicle), seats_total=seats_total)

    for name, value in changed.items():
        setattr(ride, name, value)
    ride.seats_available = seats_total - held
    ride.status = Ride.Status.FULL if ride.seats_available == 0 else Ride.Status.OPEN
    ride.save()
    if preferred is not None:
        ride.preferred_interests.set(preferred)
    if changed.keys() & MATCH_TERMS and ride.status == Ride.Status.OPEN:
        publish_on_commit("ride.updated", ride_id=str(ride.id))
    return ride


@transaction.atomic
def start_ride(*, ride: Ride, actor) -> Ride:
    """Close booking admission. Pending requests must be accepted, rejected or cancelled first."""
    from apps.bookings.state_machine import BookingStatus

    ride = _lock_owned(ride, actor)
    if ride.status not in UPCOMING:
        raise RideError(f"Only an open or full ride can start (ride is {ride.status})")
    unresolved = ride.bookings.filter(status__in=[BookingStatus.REQUESTED, BookingStatus.ACCEPTED]).count()
    if unresolved:
        noun = "booking is" if unresolved == 1 else "bookings are"
        raise RideError(f"{unresolved} {noun} still requested or awaiting payment; resolve them before starting")
    ride.status = Ride.Status.IN_PROGRESS
    ride.started_at = timezone.now()
    ride.save(update_fields=["status", "started_at", "updated_at"])
    publish_on_commit("ride.status_changed", ride_id=str(ride.id), status=ride.status)
    return ride


@transaction.atomic
def complete_ride(*, ride: Ride, actor) -> Ride:
    """Finish the trip: every still-confirmed booking completes (no-shows were marked explicitly)."""
    from apps.bookings import services as booking_services
    from apps.bookings.state_machine import BookingStatus

    ride = _lock_owned(ride, actor)
    if ride.status != Ride.Status.IN_PROGRESS:
        raise RideError(f"Only an in-progress ride can be completed (ride is {ride.status})")
    for booking in booking_services.active_bookings_for_update(ride):
        if booking.status == BookingStatus.CONFIRMED:
            booking_services._apply_locked(ride=ride, booking=booking, action="complete", actor=actor)
    ride.status = Ride.Status.COMPLETED
    ride.completed_at = timezone.now()
    ride.save(update_fields=["status", "completed_at", "updated_at"])
    publish_on_commit("ride.status_changed", ride_id=str(ride.id), status=ride.status)
    return ride


def cancellation_quote(*, ride: Ride, actor) -> dict:
    from apps.bookings.services import ACTIVE_STATUSES, refund_breakdown

    if ride.driver_id != actor.id:
        raise NotRideOwner("Only the ride's driver can manage it")
    bookings = list(ride.bookings.filter(status__in=ACTIVE_STATUSES).order_by("pk"))
    rows = [{"booking": str(b.id), "status": b.status, **refund_breakdown(b)} for b in bookings]
    return {
        "allowed": ride.status in UPCOMING,
        "status": ride.status,
        "bookings_cancelled": len(rows),
        "paid_bookings": sum(1 for row in rows if row["status"] == "CONFIRMED"),
        "total_refund": sum((row["refund"] for row in rows), start=Decimal("0.000")),
        "bookings": rows,
    }


@transaction.atomic
def cancel_ride(*, ride: Ride, actor) -> Ride:
    """Cancel every active booking (refunding paid ones exactly once), then the ride itself."""
    ride = _lock_owned(ride, actor)
    return _cancel_locked(ride=ride, actor=actor)


def _cancel_locked(*, ride: Ride, actor, staff_override: bool = False) -> Ride:
    from apps.bookings import services as booking_services

    if ride.status not in UPCOMING:
        raise RideError(f"Only an open or full ride can be cancelled (ride is {ride.status})")
    for booking in booking_services.active_bookings_for_update(ride):
        booking_services._apply_locked(
            ride=ride, booking=booking, action="cancel_driver", actor=actor, staff_override=staff_override,
        )
    ride.refresh_from_db()
    ride.status = Ride.Status.CANCELLED
    ride.cancelled_at = timezone.now()
    ride.save(update_fields=["status", "cancelled_at", "updated_at"])
    publish_on_commit("ride.status_changed", ride_id=str(ride.id), status=ride.status)
    return ride


def staff_cancel_ride(*, ride: Ride, staff, reason: str = "") -> Ride:
    """Authorized investigation/override path: the same refund and seat invariants as an owner cancel.

    Only staff reach this; it never bypasses the ledger, it only skips the ownership
    check so a compromised or absent driver cannot strand paid passengers.
    """
    from apps.audit.services import record_action

    with transaction.atomic():
        ride = Ride.objects.select_for_update(of=("self",)).select_related("vehicle").get(pk=ride.pk)
        ride = _cancel_locked(ride=ride, actor=staff, staff_override=True)
        record_action(
            actor=staff, action="ride.cancelled_by_staff", object_type="ride", object_id=ride.pk,
            reason=reason, metadata={"driver_id": str(ride.driver_id), "status": ride.status},
        )
        return ride


def staff_complete_ride(*, ride: Ride, staff, reason: str = "") -> Ride:
    """Authorized override for a trip whose driver never marked it complete."""
    from apps.audit.services import record_action
    from apps.bookings import services as booking_services
    from apps.bookings.state_machine import BookingStatus

    with transaction.atomic():
        ride = Ride.objects.select_for_update(of=("self",)).select_related("vehicle").get(pk=ride.pk)
        if ride.status != Ride.Status.IN_PROGRESS:
            raise RideError(f"Only an in-progress ride can be completed (ride is {ride.status})")
        for booking in booking_services.active_bookings_for_update(ride):
            if booking.status == BookingStatus.CONFIRMED:
                booking_services._apply_locked(
                    ride=ride, booking=booking, action="complete", actor=staff, staff_override=True,
                )
        ride.status = Ride.Status.COMPLETED
        ride.completed_at = timezone.now()
        ride.save(update_fields=["status", "completed_at", "updated_at"])
        publish_on_commit("ride.status_changed", ride_id=str(ride.id), status=ride.status)
        record_action(
            actor=staff, action="ride.completed_by_staff", object_type="ride", object_id=ride.pk,
            reason=reason, metadata={"driver_id": str(ride.driver_id), "status": ride.status},
        )
        return ride


@transaction.atomic
def reserve_seats(*, ride_id, seats: int) -> Ride:
    """Atomically decrement seats. The check constraint is the final guard against overbooking."""
    ride = Ride.objects.select_for_update().get(pk=ride_id)
    if ride.status != Ride.Status.OPEN:
        raise ValueError("Ride is not open for booking")
    if ride.seats_available < seats:
        raise ValueError("Not enough seats available")

    ride.seats_available = F("seats_available") - seats
    ride.save(update_fields=["seats_available", "updated_at"])
    ride.refresh_from_db()
    if ride.seats_available == 0:
        ride.status = Ride.Status.FULL
        ride.save(update_fields=["status", "updated_at"])
    return ride


@transaction.atomic
def release_seats(*, ride_id, seats: int) -> Ride:
    """Return seats to the inventory. Only a FULL ride reopens; started or closed rides stay closed."""
    ride = Ride.objects.select_for_update().get(pk=ride_id)
    ride.seats_available = F("seats_available") + seats
    ride.save(update_fields=["seats_available", "updated_at"])
    ride.refresh_from_db()
    if ride.status == Ride.Status.FULL and ride.seats_available > 0:
        ride.status = Ride.Status.OPEN
        ride.save(update_fields=["status", "updated_at"])
    return ride
