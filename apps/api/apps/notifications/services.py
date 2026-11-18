import logging

from django.db import transaction
from django.utils import timezone

from .models import DeviceToken, Notification, NotificationDelivery, NotificationPreference
from .templates import render_notification

logger = logging.getLogger("blasa.notifications")


def preferences_for(user):
    return NotificationPreference.objects.get_or_create(user=user)[0]


def _queue_delivery(delivery_id):
    from .tasks import deliver_notification

    try:
        deliver_notification.delay(str(delivery_id))
    except Exception:
        logger.warning("Notification delivery queue unavailable; persisted delivery awaits sweep")


@transaction.atomic
def notify(
    *, user, kind: str, event_key: str = "", status: str | None = None,
    payload: dict | None = None, channels: tuple[str, ...] = ("in_app", "push"),
    promotional: bool = False,
) -> Notification | None:
    if not user.is_active:
        return None
    preferences = preferences_for(user)
    if promotional and not preferences.promotions_enabled:
        return None
    payload = {**(payload or {})}
    if status:
        payload["status"] = status
    if promotional:
        payload["promotional"] = True
    title, body = render_notification(
        kind=kind, status=status, preferred_language=user.preferred_language, payload=payload,
    )
    if not title and not body:
        return None
    defaults = {"kind": kind, "title": title or body, "body": body, "payload": payload}
    if event_key:
        notification, created = Notification.objects.get_or_create(user=user, event_key=event_key, defaults=defaults)
        if not created:
            return notification
    else:
        notification = Notification.objects.create(user=user, **defaults)
    destinations = []
    if "push" in channels and preferences.push_enabled:
        destinations.extend(("push", d.token, d.provider) for d in DeviceToken.objects.filter(user=user))
    if "email" in channels and preferences.email_enabled and user.email_verified_at and user.email:
        destinations.append(("email", user.email, ""))
    if "sms" in channels and preferences.sms_enabled and user.phone_verified_at and user.phone_number:
        destinations.append(("sms", user.phone_number, ""))
    for channel, destination, provider in destinations:
        delivery = NotificationDelivery.objects.create(
            notification=notification, channel=channel, destination=destination, provider=provider,
        )
        transaction.on_commit(lambda pk=delivery.pk: _queue_delivery(pk))
    return notification


def delivery_eligible(delivery):
    user = delivery.notification.user
    preferences = preferences_for(user)
    if not user.is_active or not getattr(preferences, f"{delivery.channel}_enabled"):
        return False
    payload = delivery.notification.payload
    if payload.get("promotional") and not preferences.promotions_enabled:
        return False
    if delivery.channel == "push" and not DeviceToken.objects.filter(
        user=user, token=delivery.destination, provider=delivery.provider,
    ).exists():
        return False
    if delivery.channel == "email" and (not user.email_verified_at or user.email != delivery.destination):
        return False
    if delivery.channel == "sms" and (not user.phone_verified_at or user.phone_number != delivery.destination):
        return False
    if delivery.notification.kind == "ride.reminder":
        from apps.bookings.models import Booking
        from apps.rides.models import Ride

        ride = Ride.objects.filter(pk=payload["ride_id"], status__in=[Ride.Status.OPEN, Ride.Status.FULL]).first()
        if ride is None or ride.departure_at.isoformat() != payload["departure_at"] or ride.departure_at <= timezone.now():
            return False
        booking_id = payload.get("booking_id")
        if booking_id and not Booking.objects.filter(pk=booking_id, passenger=user, status="CONFIRMED").exists():
            return False
        if not booking_id and ride.driver_id != user.pk:
            return False
    return True


def mark_read(*, notification: Notification, user) -> Notification:
    if notification.user_id != user.id:
        raise PermissionError("Not your notification")
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at", "updated_at"])
    return notification


def register_device(*, user, token: str, platform: str, provider: str = DeviceToken.Provider.FCM) -> DeviceToken:
    device, _ = DeviceToken.objects.get_or_create(
        token=token, defaults={"user": user, "platform": platform, "provider": provider},
    )
    if device.user_id != user.id:
        raise PermissionError("This device is already registered")
    if device.platform != platform or device.provider != provider:
        device.platform, device.provider = platform, provider
        device.save(update_fields=["platform", "provider", "updated_at"])
    return device


def unregister_device(*, user, token: str) -> None:
    DeviceToken.objects.filter(user=user, token=token).delete()
