"""Moderation workflow: file/review reports, ban and unban users.

Bans work by deactivating the account: simplejwt refuses tokens for inactive
users, so the ban takes effect on the user's next request. Every staff decision
(report review, ban, unban, ride override) also writes an AuditLog row through
apps.audit.services.record_action, so the decision trail survives the objects it
acted on.
"""

import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_action
from core.events import publish

from .models import Ban, Report


class ModerationError(ValueError):
    """Domain rule violation; the view layer maps this to HTTP 400."""


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _check_target(*, reporter, target_type: str, target_id) -> str:
    """Validate that the target exists and the reporter may report it.

    Historical report references stay meaningful: rows are never deleted, and a
    target that has since disappeared still fails validation at filing time
    rather than silently attaching to nothing.
    """
    from apps.messaging.models import Message
    from apps.rides.models import Ride
    from apps.vehicles.models import Vehicle

    target_type = (target_type or "").strip().upper()
    if target_type not in Report.TargetType.values:
        raise ModerationError("Choose a valid report target type")
    identifier = _uuid(target_id)
    if identifier is None:
        raise ModerationError("The reported item identifier is not valid")

    if target_type == Report.TargetType.USER:
        if not get_user_model().objects.filter(pk=identifier).exists():
            raise ModerationError("The reported member no longer exists")
        if identifier == reporter.pk:
            raise ModerationError("You cannot report yourself")
        return str(identifier)

    if target_type == Report.TargetType.RIDE:
        ride = Ride.objects.filter(pk=identifier).values("id", "driver_id").first()
        if ride is None:
            raise ModerationError("The reported ride no longer exists")
        if ride["driver_id"] != reporter.pk:
            from apps.bookings.models import Booking

            if not Booking.objects.filter(ride_id=identifier, passenger=reporter).exists():
                raise ModerationError("You can only report rides you took part in")
        return str(identifier)

    if target_type == Report.TargetType.VEHICLE:
        vehicle = Vehicle.objects.filter(pk=identifier).values("id", "driver_id").first()
        if vehicle is None:
            raise ModerationError("The reported vehicle no longer exists")
        if vehicle["driver_id"] != reporter.pk:
            from apps.bookings.models import Booking

            if not Booking.objects.filter(
                ride__vehicle_id=identifier, passenger=reporter,
            ).exists():
                raise ModerationError("You can only report vehicles from rides you took part in")
        return str(identifier)

    # MESSAGE
    message = Message.objects.filter(pk=identifier).values("id", "conversation__booking_id").first()
    if message is None:
        raise ModerationError("The reported message no longer exists")
    from apps.bookings.models import Booking

    booking = Booking.objects.filter(pk=message["conversation__booking_id"]).values(
        "passenger_id", "ride__driver_id",
    ).first()
    if booking is None or reporter.pk not in {booking["passenger_id"], booking["ride__driver_id"]}:
        raise ModerationError("You can only report messages from your own conversations")
    return str(identifier)


def file_report(*, reporter, target_type: str, target_id: str, reason: str, details: str = "") -> Report:
    target_id = _check_target(reporter=reporter, target_type=target_type, target_id=target_id)
    if reason not in Report.Reason.values:
        raise ModerationError("Choose a valid report reason")
    details = (details or "").strip()
    if len(details) > 2000:
        raise ModerationError("Details must not exceed 2000 characters")
    if Report.objects.filter(
        reporter=reporter, target_type=target_type, target_id=target_id, status=Report.Status.OPEN
    ).exists():
        raise ModerationError("You already have an open report for this target")
    return Report.objects.create(
        reporter=reporter, target_type=target_type, target_id=target_id, reason=reason, details=details
    )


@transaction.atomic
def review_report(*, report: Report, decision: str, staff, reason: str = "") -> Report:
    report = Report.objects.select_for_update().get(pk=report.pk)
    if report.status != Report.Status.OPEN:
        raise ModerationError("This report was already handled")
    if decision not in (Report.Status.DISMISSED, Report.Status.ACTIONED):
        raise ModerationError("Decision must be DISMISSED or ACTIONED")
    report.status = decision
    report.handled_by = staff
    report.handled_at = timezone.now()
    report.save(update_fields=["status", "handled_by", "handled_at", "updated_at"])
    record_action(
        actor=staff, action=f"report.{decision.lower()}", object_type="report", object_id=report.pk,
        reason=reason, metadata={"target_type": report.target_type, "target_id": report.target_id,
                                 "filed_reason": report.reason},
    )
    return report


@transaction.atomic
def ban_user(*, user_id, reason: str, staff) -> Ban:
    user = get_user_model().objects.select_for_update().get(pk=user_id)
    if Ban.objects.filter(user=user, revoked_at__isnull=True).exists():
        raise ModerationError("User is already banned")
    if user.is_superuser:
        raise ModerationError("Superusers cannot be banned")
    ban = Ban.objects.create(user=user, reason=reason, issued_by=staff)
    user.is_active = False
    user.save(update_fields=["is_active"])
    record_action(
        actor=staff, action="user.banned", object_type="user", object_id=user.pk, reason=reason,
        metadata={"ban_id": str(ban.pk), "role": user.role},
    )
    publish("moderation.banned", user_id=str(user.id), ban_id=str(ban.id))
    return ban


@transaction.atomic
def unban_user(*, user_id, staff, reason: str = "") -> None:
    user = get_user_model().objects.select_for_update().get(pk=user_id)
    ban = Ban.objects.filter(user=user, revoked_at__isnull=True).first()
    if ban:
        ban.revoked_at = timezone.now()
        ban.revoked_by = staff
        ban.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    user.is_active = True
    user.save(update_fields=["is_active"])
    record_action(
        actor=staff, action="user.unbanned", object_type="user", object_id=user.pk, reason=reason,
        metadata={"ban_id": str(ban.pk) if ban else ""},
    )
    publish("moderation.unbanned", user_id=str(user.id))
