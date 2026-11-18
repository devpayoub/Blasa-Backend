import json
from datetime import timedelta
from urllib.request import Request, urlopen

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import DeviceToken, NotificationDelivery
from .services import delivery_eligible, notify

_fcm_app = None


class TransportUnavailable(Exception):
    pass


class InvalidDevice(Exception):
    pass


def _send_delivery(delivery):
    notification = delivery.notification
    if delivery.channel == "sms":
        raise TransportUnavailable("SMS adapter is not contracted")
    if delivery.channel == "email":
        if not settings.NOTIFICATION_EMAIL_ENABLED:
            raise TransportUnavailable("Notification email is disabled")
        sent = send_mail(notification.title, notification.body, settings.DEFAULT_FROM_EMAIL, [delivery.destination])
        if sent != 1:
            raise RuntimeError("Email not accepted")
        return ""
    payload = {"notification_id": str(notification.pk), "kind": notification.kind}
    payload.update({k: str(v) for k, v in notification.payload.items()
                    if k in {"booking_id", "ride_id", "intent_id", "award_id"}})
    if delivery.provider == DeviceToken.Provider.EXPO:
        if not settings.EXPO_PUSH_ENABLED:
            raise TransportUnavailable("Expo push is disabled")
        request = Request(
            "https://exp.host/--/api/v2/push/send",
            data=json.dumps({"to": delivery.destination, "title": notification.title, "body": notification.body,
                             "data": payload, "sound": "default"}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=10) as response:
            ticket = json.loads(response.read(65536)).get("data", {})
        if ticket.get("status") != "ok":
            if ticket.get("details", {}).get("error") == "DeviceNotRegistered":
                raise InvalidDevice()
            raise RuntimeError("Expo push not accepted")
        return ticket["id"]
    if not settings.FCM_CREDENTIALS_PATH:
        raise TransportUnavailable("FCM is not configured")
    global _fcm_app
    import firebase_admin
    from firebase_admin import credentials, messaging

    if _fcm_app is None:
        _fcm_app = firebase_admin.initialize_app(credentials.Certificate(settings.FCM_CREDENTIALS_PATH))
    try:
        return messaging.send(messaging.Message(
            notification=messaging.Notification(title=notification.title, body=notification.body),
            data=payload, token=delivery.destination,
        ))
    except messaging.UnregisteredError as exc:
        raise InvalidDevice() from exc


@shared_task
@transaction.atomic
def deliver_notification(delivery_id):
    delivery = NotificationDelivery.objects.select_for_update(of=("self",)).select_related(
        "notification__user",
    ).filter(pk=delivery_id).first()
    if delivery is None or delivery.status in {"SENT", "SKIPPED", "FAILED"}:
        return
    now = timezone.now()
    if delivery.next_attempt_at and delivery.next_attempt_at > now:
        return
    if not delivery_eligible(delivery):
        delivery.status = NotificationDelivery.Status.SKIPPED
        delivery.last_error = "Recipient or event is no longer eligible"
    else:
        try:
            delivery.external_id = _send_delivery(delivery)
        except TransportUnavailable as exc:
            delivery.status = NotificationDelivery.Status.BLOCKED
            delivery.last_error = str(exc)
        except InvalidDevice:
            DeviceToken.objects.filter(user=delivery.notification.user, token=delivery.destination).delete()
            delivery.status = NotificationDelivery.Status.SKIPPED
            delivery.last_error = "Device is no longer registered"
        except Exception as exc:
            delivery.attempts += 1
            delivery.status = NotificationDelivery.Status.FAILED if delivery.attempts >= 6 else NotificationDelivery.Status.PENDING
            delivery.next_attempt_at = now + timedelta(seconds=min(3600, 30 * 2 ** delivery.attempts))
            delivery.last_error = type(exc).__name__[:120]
        else:
            delivery.attempts += 1
            delivery.status = NotificationDelivery.Status.SENT
            delivery.last_error = ""
            delivery.next_attempt_at = None
    delivery.save(update_fields=["status", "attempts", "next_attempt_at", "last_error", "external_id", "updated_at"])


@shared_task
def retry_deliveries():
    due = NotificationDelivery.objects.filter(status__in=["PENDING", "BLOCKED"]).filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=timezone.now()),
    ).order_by("created_at").values_list("pk", flat=True)[:500]
    for pk in due:
        deliver_notification.delay(str(pk))


@shared_task
def schedule_ride_reminders():
    from apps.rides.models import Ride

    now = timezone.now()
    ride_ids = Ride.objects.filter(
        status__in=[Ride.Status.OPEN, Ride.Status.FULL], departure_at__gt=now,
        departure_at__lte=now + timedelta(hours=1),
    ).values_list("pk", flat=True)
    count = 0
    for ride_id in ride_ids:
        with transaction.atomic():
            ride = Ride.objects.select_for_update().select_related("driver").get(pk=ride_id)
            if ride.status not in {Ride.Status.OPEN, Ride.Status.FULL} or not now < ride.departure_at <= now + timedelta(hours=1):
                continue
            departure = ride.departure_at.isoformat()
            payload = {"ride_id": str(ride.pk), "departure_at": departure, "origin": ride.origin_label,
                       "destination": ride.destination_label,
                       "departure_time": timezone.localtime(ride.departure_at).strftime("%H:%M")}
            recipients = [(ride.driver, None)]
            recipients.extend((b.passenger, b.pk) for b in ride.bookings.select_related("passenger").filter(status="CONFIRMED"))
            for user, booking_id in recipients:
                key = f"reminder:{ride.pk}:{departure}:{booking_id or 'driver'}:{user.pk}"
                notification = notify(
                    user=user, kind="ride.reminder", event_key=key,
                    payload={**payload, **({"booking_id": str(booking_id)} if booking_id else {})},
                    channels=("in_app", "push", "email", "sms"),
                )
                if notification:
                    count += 1
    return count
