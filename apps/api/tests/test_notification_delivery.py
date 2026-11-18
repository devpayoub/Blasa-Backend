from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from logging import LogRecord
from unittest.mock import patch

import pytest
from django.db import close_old_connections, transaction
from django.utils import timezone
from rest_framework.test import APIClient
from test_phase6_flow import _make_booking, _make_driver, _make_ride

from apps.bookings.models import Booking
from apps.notifications.models import DeviceToken, Notification, NotificationDelivery, NotificationPreference
from apps.notifications.services import notify, preferences_for
from apps.notifications.tasks import deliver_notification, retry_deliveries, schedule_ride_reminders
from apps.payments.models import PaymentIntent
from apps.payments.services import settle_topup
from apps.rides.models import Ride
from apps.users.models import User
from apps.verification.models import VerificationDocument, VerificationReview
from apps.verification.services import review_document
from apps.wallet.models import LedgerEntry
from core.events import publish, publish_on_commit
from core.log_filters import RedactTokenFilter


@pytest.fixture
def recipient():
    user = User.objects.create_user(phone_number="+21660200001", password="x", preferred_language="en")
    preferences_for(user)
    return user


def _delivery(user, channel="push", provider="FCM", **payload):
    preferences = preferences_for(user)
    setattr(preferences, f"{channel}_enabled", True)
    preferences.save()
    DeviceToken.objects.get_or_create(user=user, token="test-token", defaults={"provider": provider})
    notification = notify(user=user, kind="match.found", event_key="test-match", payload=payload, channels=("in_app",))
    return NotificationDelivery.objects.create(
        notification=notification, channel=channel, destination="test-token", provider=provider,
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.mark.django_db
def test_transactional_inbox_and_delivery_roll_back_without_dispatch(recipient):
    preferences = preferences_for(recipient)
    preferences.push_enabled = True
    preferences.save()
    DeviceToken.objects.create(user=recipient, token="test-token")
    with patch("apps.notifications.services._queue_delivery") as queue:
        with pytest.raises(RuntimeError), transaction.atomic():
            publish_on_commit("match.found", interest_id="route", ride_id="ride", passenger_id=recipient.pk)
            assert Notification.objects.filter(user=recipient).count() == 1
            assert NotificationDelivery.objects.count() == 1
            queue.assert_not_called()
            raise RuntimeError("rollback")
        queue.assert_not_called()
    assert not Notification.objects.exists()
    assert not NotificationDelivery.objects.exists()


@pytest.mark.django_db
def test_replay_deduplicates_inbox_and_deliveries_after_read(recipient, django_capture_on_commit_callbacks):
    preferences = preferences_for(recipient)
    preferences.push_enabled = True
    preferences.save()
    DeviceToken.objects.create(user=recipient, token="test-token")
    with patch("apps.notifications.services._queue_delivery") as queue:
        with django_capture_on_commit_callbacks(execute=True):
            for _ in range(3):
                publish("match.found", interest_id="route", ride_id="ride", passenger_id=recipient.pk)
        queue.assert_called_once()
    note = Notification.objects.get()
    note.read_at = timezone.now()
    note.save()
    publish("match.found", interest_id="route", ride_id="ride", passenger_id=recipient.pk)
    assert Notification.objects.count() == 1
    assert NotificationDelivery.objects.count() == 1
    assert Notification.objects.get().read_at is not None


@pytest.mark.django_db
def test_delivery_retries_persist_and_success_is_not_resent(recipient):
    delivery = _delivery(recipient)
    with patch("apps.notifications.tasks._send_delivery", side_effect=TimeoutError):
        deliver_notification(str(delivery.pk))
    delivery.refresh_from_db()
    assert delivery.status == "PENDING" and delivery.attempts == 1
    assert delivery.next_attempt_at > timezone.now()
    with patch("apps.notifications.tasks._send_delivery", return_value="transport-id") as send:
        deliver_notification(str(delivery.pk))
        send.assert_not_called()
        NotificationDelivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        deliver_notification(str(delivery.pk))
        deliver_notification(str(delivery.pk))
        send.assert_called_once()
    delivery.refresh_from_db()
    assert delivery.status == "SENT" and delivery.external_id == "transport-id" and delivery.attempts == 2


@pytest.mark.django_db
def test_retry_exhaustion_is_not_silent(recipient):
    delivery = _delivery(recipient)
    with patch("apps.notifications.tasks._send_delivery", side_effect=TimeoutError):
        for _ in range(6):
            NotificationDelivery.objects.filter(pk=delivery.pk).update(next_attempt_at=None)
            deliver_notification(str(delivery.pk))
    delivery.refresh_from_db()
    assert delivery.status == "FAILED" and delivery.attempts == 6
    assert delivery.last_error == "TimeoutError"


@pytest.mark.django_db
def test_disabled_transport_is_blocked_not_sent_and_can_recover(recipient, settings):
    settings.FCM_CREDENTIALS_PATH = None
    delivery = _delivery(recipient)
    deliver_notification(str(delivery.pk))
    delivery.refresh_from_db()
    assert delivery.status == "BLOCKED" and delivery.attempts == 0
    with patch("apps.notifications.tasks._send_delivery", return_value="accepted"):
        retry_deliveries()
    delivery.refresh_from_db()
    assert delivery.status == "SENT"


@pytest.mark.django_db
def test_broker_failure_leaves_recoverable_delivery(recipient, django_capture_on_commit_callbacks):
    preferences = preferences_for(recipient)
    preferences.push_enabled = True
    preferences.save()
    DeviceToken.objects.create(user=recipient, token="test-token")
    with patch("apps.notifications.tasks.deliver_notification.delay", side_effect=ConnectionError):
        with django_capture_on_commit_callbacks(execute=True):
            notify(user=recipient, kind="match.found", event_key="recoverable")
    delivery = NotificationDelivery.objects.get()
    assert delivery.status == "PENDING"
    with patch("apps.notifications.tasks._send_delivery", return_value="accepted"):
        retry_deliveries()
    delivery.refresh_from_db()
    assert delivery.status == "SENT"


@pytest.mark.django_db
@pytest.mark.parametrize("revoke", ["ban", "preference", "device"])
def test_delivery_rechecks_account_consent_and_registered_device(recipient, revoke):
    delivery = _delivery(recipient)
    if revoke == "ban":
        User.objects.filter(pk=recipient.pk).update(is_active=False)
    elif revoke == "preference":
        NotificationPreference.objects.filter(user=recipient).update(push_enabled=False)
    else:
        DeviceToken.objects.filter(user=recipient).delete()
    with patch("apps.notifications.tasks._send_delivery") as send:
        deliver_notification(str(delivery.pk))
        send.assert_not_called()
    delivery.refresh_from_db()
    assert delivery.status == "SKIPPED"


@pytest.mark.django_db
def test_promotion_requires_consent_without_changing_award_eligibility(recipient):
    assert notify(user=recipient, kind="incentive.awarded", event_key="award", promotional=True) is None
    preferences = preferences_for(recipient)
    preferences.promotions_enabled = True
    preferences.save()
    note = notify(user=recipient, kind="incentive.awarded", event_key="award", promotional=True,
                  payload={"amount": "10.000", "expires_at": "2026-12-01"})
    assert note.payload["promotional"] is True
    assert "10.000" in note.body and "2026-12-01" in note.body


@pytest.mark.django_db
def test_inbox_unread_read_all_and_preferences_are_owned(recipient):
    other = User.objects.create_user(phone_number="+21660200002", password="x")
    note = notify(user=recipient, kind="match.found", event_key="one")
    notify(user=other, kind="match.found", event_key="two")
    client = _client(recipient)
    assert client.get("/api/v1/notifications/unread-count/").data == {"count": 1}
    assert client.get("/api/v1/notifications/?unread=true").data["count"] == 1
    assert _client(other).post(f"/api/v1/notifications/{note.pk}/read/").status_code == 404
    assert client.post("/api/v1/notifications/read-all/").data == {"updated": 1}
    assert client.get("/api/v1/notifications/unread-count/").data == {"count": 0}
    assert Notification.objects.get(user=other).read_at is None
    prefs = client.get("/api/v1/notifications/preferences/").data
    assert not any(prefs[k] for k in ["push_enabled", "email_enabled", "sms_enabled", "promotions_enabled"])
    assert client.patch("/api/v1/notifications/preferences/", {"promotions_enabled": True}, format="json").status_code == 200
    assert preferences_for(recipient).promotions_enabled
    assert not preferences_for(other).promotions_enabled


@pytest.mark.django_db
def test_device_transport_validation_and_ownership(recipient):
    client = _client(recipient)
    path = "/api/v1/notifications/devices/"
    assert client.post(path, {"token": "ExpoPushToken[test]", "platform": "IOS"}, format="json").status_code == 400
    registered = client.post(path, {"token": "ExpoPushToken[test]", "platform": "ios", "provider": "EXPO"}, format="json")
    assert registered.status_code == 201 and registered.data["platform"] == "IOS"
    assert client.post(path, {"token": "fcm", "platform": "invalid"}, format="json").status_code == 400
    assert client.post(path, {"token": "ExpoPushToken[web]", "platform": "WEB", "provider": "EXPO"}, format="json").status_code == 400
    other = User.objects.create_user(phone_number="+21660200002", password="x")
    assert _client(other).post(path, {"token": "ExpoPushToken[test]", "platform": "IOS", "provider": "EXPO"}, format="json").status_code == 403
    assert _client(other).delete(path, {"token": "ExpoPushToken[test]"}, format="json").status_code == 204
    assert DeviceToken.objects.filter(user=recipient).exists()


@pytest.mark.django_db
def test_payment_and_wallet_events_follow_posted_legs_once(recipient):
    intent = PaymentIntent.objects.create(user=recipient, gateway="CASH", amount=Decimal("20.000"))
    assert settle_topup(intent=intent)
    assert not settle_topup(intent=intent)
    payment = Notification.objects.get(user=recipient, kind="payment.status_changed")
    wallet = Notification.objects.get(user=recipient, kind="wallet.posted")
    assert payment.payload["status"] == "PAID" and "20.000" in payment.body
    assert wallet.payload["amount"] == "20.000" and wallet.payload["reference"] == f"topup:{intent.pk}"
    assert LedgerEntry.objects.filter(pk=wallet.payload["entry_id"], account__owner=recipient).exists()
    assert Notification.objects.count() == 2


@pytest.mark.django_db
def test_verification_review_events_have_distinct_identity_and_safe_copy(recipient):
    staff = User.objects.create_user(phone_number="+21660200099", password="x", is_staff=True)
    doc = VerificationDocument.objects.create(user=recipient, doc_type="CIN", storage_key="synthetic")
    for _ in range(2):
        review_document(document=doc, reviewer=staff, decision=VerificationReview.Decision.REQUEST_MORE,
                        reason="Please supply a clearer scan")
    notes = Notification.objects.filter(user=recipient, kind="verification.changed")
    assert notes.count() == 2
    assert all(n.payload["reason"] == "Please supply a clearer scan" for n in notes)
    assert all("provide the requested documents" in n.body and "synthetic" not in n.body for n in notes)


@pytest.mark.django_db
def test_reminders_only_confirmed_passengers_and_driver_once():
    driver, vehicle = _make_driver("+21660200010")
    confirmed = User.objects.create_user(phone_number="+21660200011", password="x")
    requested = User.objects.create_user(phone_number="+21660200012", password="x")
    booking = _make_booking(driver, vehicle, confirmed)
    Booking.objects.filter(pk=booking.pk).update(status="CONFIRMED", paid_amount=Decimal("15.000"))
    Booking.objects.create(ride=booking.ride, passenger=requested, pickup_point=booking.pickup_point,
                           dropoff_point=booking.dropoff_point, fare_amount=Decimal("15.000"), status="ACCEPTED")
    Ride.objects.filter(pk=booking.ride_id).update(departure_at=timezone.now() + timedelta(minutes=40))
    schedule_ride_reminders()
    schedule_ride_reminders()
    notes = Notification.objects.filter(kind="ride.reminder")
    assert notes.count() == 2 and set(notes.values_list("user_id", flat=True)) == {driver.pk, confirmed.pk}
    Booking.objects.filter(pk=booking.pk).update(status="CANCELLED_BY_PASSENGER")
    schedule_ride_reminders()
    assert Notification.objects.filter(kind="ride.reminder").count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["cancel_ride", "cancel_booking", "reschedule"])
def test_stale_reminder_delivery_is_skipped(change):
    driver, vehicle = _make_driver("+21660200020")
    passenger = User.objects.create_user(phone_number="+21660200021", password="x")
    preferences = preferences_for(passenger)
    preferences.push_enabled = True
    preferences.save()
    DeviceToken.objects.create(user=passenger, token="test-token")
    booking = _make_booking(driver, vehicle, passenger)
    Booking.objects.filter(pk=booking.pk).update(status="CONFIRMED", paid_amount=Decimal("15.000"))
    Ride.objects.filter(pk=booking.ride_id).update(departure_at=timezone.now() + timedelta(minutes=40))
    schedule_ride_reminders()
    delivery = NotificationDelivery.objects.get(notification__kind="ride.reminder")
    if change == "cancel_ride":
        Ride.objects.filter(pk=booking.ride_id).update(status="CANCELLED")
    elif change == "cancel_booking":
        Booking.objects.filter(pk=booking.pk).update(status="CANCELLED_BY_PASSENGER")
    else:
        Ride.objects.filter(pk=booking.ride_id).update(departure_at=timezone.now() + timedelta(minutes=50))
    with patch("apps.notifications.tasks._send_delivery") as send:
        deliver_notification(str(delivery.pk))
        send.assert_not_called()
    delivery.refresh_from_db()
    assert delivery.status == "SKIPPED"


@pytest.mark.django_db
def test_rescheduled_driver_reminder_uses_new_departure_identity():
    driver, vehicle = _make_driver("+21660200030")
    ride = _make_ride(driver, vehicle, departure_at=timezone.now() + timedelta(minutes=40))
    schedule_ride_reminders()
    Ride.objects.filter(pk=ride.pk).update(departure_at=timezone.now() + timedelta(minutes=50))
    schedule_ride_reminders()
    assert Notification.objects.filter(kind="ride.reminder").count() == 2
    Ride.objects.filter(pk=ride.pk).update(status="CANCELLED")
    schedule_ride_reminders()
    assert Notification.objects.filter(kind="ride.reminder").count() == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_event_replay_creates_one_inbox(recipient):
    def record():
        close_old_connections()
        try:
            return notify(user=recipient, kind="match.found", event_key="concurrent").pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: record(), range(2)))
    assert ids[0] == ids[1]
    assert Notification.objects.count() == 1


def test_access_logging_redacts_query_tokens_but_preserves_path():
    record = LogRecord("uvicorn.access", 20, "", 1, "GET %s", ("/ws/chat/id/?token=synthetic-jwt&lang=en",), None)
    assert RedactTokenFilter().filter(record)
    assert record.getMessage() == "GET /ws/chat/id/?token=[redacted]&lang=en"
    assert "synthetic-jwt" not in str(record.args)


@pytest.mark.django_db
def test_email_delivery_uses_test_mailbox_only(recipient, settings):
    from django.core import mail

    settings.NOTIFICATION_EMAIL_ENABLED = True
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.DEFAULT_FROM_EMAIL = "synthetic@example.test"
    recipient.email = "recipient@example.test"
    recipient.email_verified_at = timezone.now()
    recipient.save()
    delivery = _delivery(recipient, channel="email")
    delivery.destination = recipient.email
    delivery.save()
    deliver_notification(str(delivery.pk))
    delivery.refresh_from_db()
    assert delivery.status == "SENT"
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [recipient.email]
    assert mail.outbox[0].subject == "Ride match"


@pytest.mark.django_db
def test_expo_transport_acceptance_and_invalid_device_are_recorded(recipient, settings):
    import json
    from unittest.mock import MagicMock

    settings.EXPO_PUSH_ENABLED = True
    delivery = _delivery(recipient, provider="EXPO", reason="Private review reason")
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps({"data": {"status": "ok", "id": "test-ticket"}}).encode()
    with patch("apps.notifications.tasks.urlopen", return_value=response) as send:
        deliver_notification(str(delivery.pk))
        request = send.call_args.args[0]
        assert request.full_url == "https://exp.host/--/api/v2/push/send"
        assert "Private review reason" not in request.data.decode()
    delivery.refresh_from_db()
    assert delivery.status == "SENT" and delivery.external_id == "test-ticket"
    NotificationDelivery.objects.filter(pk=delivery.pk).update(status="PENDING")
    response.__enter__.return_value.read.return_value = json.dumps({
        "data": {"status": "error", "details": {"error": "DeviceNotRegistered"}},
    }).encode()
    with patch("apps.notifications.tasks.urlopen", return_value=response):
        deliver_notification(str(delivery.pk))
    delivery.refresh_from_db()
    assert delivery.status == "SKIPPED" and not DeviceToken.objects.filter(user=recipient).exists()


@pytest.mark.django_db
def test_funded_reward_notifies_only_consented_user_and_retains_award(recipient):
    from apps.incentives.models import IncentiveAward, IncentiveCampaign
    from apps.incentives.services import evaluate_user, fund_incentive_campaign

    VerificationDocument.objects.create(user=recipient, doc_type="CIN", status="APPROVED", storage_key="synthetic")
    campaign = IncentiveCampaign.objects.create(name="Synthetic", kind="WELCOME_CREDIT", is_active=True, amount=Decimal("10"))
    fund_incentive_campaign(campaign=campaign, amount=Decimal("30"))
    assert len(evaluate_user(recipient.pk)) == 1
    assert IncentiveAward.objects.filter(user=recipient).exists()
    assert not Notification.objects.filter(user=recipient, kind="incentive.awarded").exists()
    consenting = User.objects.create_user(phone_number="+21660200002", password="x", preferred_language="en")
    preferences = preferences_for(consenting)
    preferences.promotions_enabled = True
    preferences.save()
    VerificationDocument.objects.create(user=consenting, doc_type="CIN", status="APPROVED", storage_key="synthetic")
    assert len(evaluate_user(consenting.pk)) == 1
    assert len(evaluate_user(consenting.pk)) == 0
    notification = Notification.objects.get(user=consenting, kind="incentive.awarded")
    award = IncentiveAward.objects.get(user=consenting)
    assert notification.payload["award_id"] == str(award.pk)
    assert notification.payload["expires_at"] == award.expires_at.date().isoformat()
