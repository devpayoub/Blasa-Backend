import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bookings.models import Booking
from apps.payments import services as payment_services
from apps.payments.gateways.flouci import FlouciGateway
from apps.payments.models import PaymentIntent, WebhookEvent
from apps.payments.serializers import PaymentIntentSerializer
from apps.rides import services as ride_services
from apps.users.models import User
from apps.vehicles.models import Vehicle
from apps.verification.models import VerificationDocument
from apps.wallet.models import LedgerEntry, Transaction

PICKUP = Point(10.18, 36.80, srid=4326)
DROPOFF = Point(10.64, 35.83, srid=4326)
FARE = Decimal("15.000")  # price per seat
FEE = (FARE * Decimal("0.100")).quantize(Decimal("0.001"))  # 10% platform fee


def _make_driver(phone: str):
    driver = User.objects.create_user(phone_number=phone, password="x", role=User.Role.DRIVER)
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE, storage_key=f"verification/{phone}.pdf",
        status=VerificationDocument.Status.APPROVED,
    )
    vehicle = Vehicle.objects.create(
        driver=driver, make="Dacia", model="Logan", plate_number=f"{phone[-4:]} TU 2222",
        seats=4, carte_grise=document,
    )
    return driver, vehicle


def _make_ride(driver, vehicle, seats_total=2):
    return ride_services.publish_ride(
        driver=driver, vehicle=vehicle,
        origin=PICKUP, destination=DROPOFF,
        origin_label="Tunis", destination_label="Sousse",
        departure_at=timezone.now() + timedelta(days=1),
        seats_total=seats_total, price_per_seat=FARE,
    )


def _create_booking(client, ride, seats=1):
    return client.post(
        "/api/v1/bookings/",
        {"ride": str(ride.id), "seats": seats,
         "pickup_point": {"type": "Point", "coordinates": [10.20, 36.79]},
         "dropoff_point": {"type": "Point", "coordinates": [10.60, 35.85]}},
        format="json",
    )


def _transition(client, booking, action):
    return client.post(f"/api/v1/bookings/{booking.id}/transitions/{action}/")


def _pay(client, booking):
    """Passenger approves the server quote, then confirms with exactly that amount."""
    quote = client.get(f"/api/v1/bookings/{booking.id}/payment-quote/")
    assert quote.status_code == 200, quote.data
    return client.post(
        f"/api/v1/bookings/{booking.id}/transitions/confirm/",
        {"expected_out_of_pocket": str(quote.data["out_of_pocket"])}, format="json",
    )


def _start(client, booking):
    return client.post(f"/api/v1/rides/{booking.ride_id}/start/")


def _fund_wallet(user, amount: str):
    intent, _ = payment_services.create_topup(
        user=user, amount=Decimal(amount), gateway=PaymentIntent.Gateway.CASH,
    )
    return PaymentIntentSerializer(intent).data


@pytest.mark.django_db
def test_confirmed_booking_posts_balanced_ride_payment():
    driver, vehicle = _make_driver("+21640000001")
    ride = _make_ride(driver, vehicle, seats_total=2)
    passenger = User.objects.create_user(phone_number="+21640000002", password="x")
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)

    _fund_wallet(passenger, "50.000")
    booking = Booking.objects.get(pk=_create_booking(passenger_client, ride, seats=1).data["id"])
    assert _transition(driver_client, booking, "accept").status_code == 200
    assert _pay(passenger_client, booking).status_code == 200

    txn = Transaction.objects.get(reference=f"ride_payment:{booking.id}")
    assert txn.kind == "RIDE_PAYMENT"
    amounts = sorted(e.amount for e in txn.entries.all())
    # Passenger -15.000, driver +13.500, platform +1.500 — and the set balances to zero.
    assert amounts == [Decimal("-15.000"), Decimal("1.500"), Decimal("13.500")]
    assert sum(e.amount for e in txn.entries.all()) == 0


@pytest.mark.django_db
def test_confirm_without_funded_wallet_is_refused():
    driver, vehicle = _make_driver("+21640000003")
    ride = _make_ride(driver, vehicle, seats_total=2)
    passenger = User.objects.create_user(phone_number="+21640000004", password="x")
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)

    booking = Booking.objects.get(pk=_create_booking(passenger_client, ride, seats=1).data["id"])
    assert _transition(driver_client, booking, "accept").status_code == 200
    response = _pay(passenger_client, booking)
    assert response.status_code == 409
    assert "insufficient" in response.data["detail"].lower()

    # The transition rolled back entirely: still ACCEPTED, no ledger row.
    booking.refresh_from_db()
    assert booking.status == "ACCEPTED"
    assert not Transaction.objects.filter(reference=f"ride_payment:{booking.id}").exists()


@pytest.mark.django_db
def test_cash_topup_credits_staff_wallet_immediately():
    client = APIClient()
    client.force_authenticate(User.objects.create_user(phone_number="+21640000005", password="x", is_staff=True))
    response = client.post(
        "/api/v1/payments/topup/", {"amount": "25.000", "gateway": "CASH"}, format="json",
    )
    assert response.status_code == 201
    assert response.data["status"] == "PAID"

    balance = client.get("/api/v1/wallet/me/")
    assert balance.status_code == 200
    assert balance.data["balance"] == Decimal("25.000")

    history = client.get("/api/v1/wallet/entries/")
    assert history.status_code == 200
    entry = history.data["results"][0]
    assert entry["kind"] == "TOPUP"
    assert Decimal(entry["amount"]) == Decimal("25.000")


@pytest.mark.django_db
@pytest.mark.parametrize("role", [User.Role.PASSENGER, User.Role.DRIVER])
def test_cash_topup_is_forbidden_for_non_staff_without_side_effects(role):
    user = User.objects.create_user(phone_number="+21640000011", password="x", role=role)
    assert not user.is_staff
    client = APIClient()
    client.force_authenticate(user)
    assert client_balance(user) == 0
    intent_count = PaymentIntent.objects.count()
    transaction_count = Transaction.objects.count()
    entry_count = LedgerEntry.objects.count()

    response = client.post(
        "/api/v1/payments/topup/", {"amount": "25.000", "gateway": "CASH"}, format="json",
    )

    assert response.status_code == 403
    assert PaymentIntent.objects.count() == intent_count
    assert Transaction.objects.count() == transaction_count
    assert LedgerEntry.objects.count() == entry_count
    assert client_balance(user) == 0


@pytest.mark.django_db
def test_flouci_topup_settles_via_webhook_and_duplicates_are_noops():
    client = APIClient()
    client.force_authenticate(User.objects.create_user(phone_number="+21640000006", password="x"))

    # The gateway would be contacted here; stub the HTTP hop and simulate acceptance.
    with patch.object(FlouciGateway, "create_intent", autospec=True) as mock_create:
        def fake_create(self, intent):
            intent.gateway_reference = "pay_flouci_123"
            intent.save(update_fields=["gateway_reference", "updated_at"])
            return {"payment_url": "https://pay.flouci.com/pay/pay_flouci_123", "gateway_reference": "pay_flouci_123"}

        mock_create.side_effect = fake_create
        data = client.post("/api/v1/payments/topup/", {"amount": "30.000", "gateway": "FLOUCI"}, format="json")
    assert data.status_code == 201
    assert data.data["payment_url"] == "https://pay.flouci.com/pay/pay_flouci_123"
    # Not settled yet — the wallet stays empty until the webhook confirms.
    assert client.get("/api/v1/wallet/me/").data["balance"] == 0

    payload = {
        "event_id": "evt_0001",
        "event": "payment.succeeded",
        "data": {"payment": {"id": "pay_flouci_123", "amount": "30.000"}},
    }
    body = json.dumps(payload).encode()
    signature = hmac.new(settings.FLUOCI_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()

    first = APIClient().post(
        "/api/v1/payments/webhooks/flouci/", body, content_type="application/json",
        HTTP_X_FLOUCI_SIGNATURE=signature,
    )
    assert first.status_code == 200
    assert client.get("/api/v1/wallet/me/").data["balance"] == Decimal("30.000")
    intent = PaymentIntent.objects.get(gateway_reference="pay_flouci_123")
    assert intent.status == "PAID"

    # Duplicate delivery: accepted but a complete no-op.
    second = APIClient().post(
        "/api/v1/payments/webhooks/flouci/", body, content_type="application/json",
        HTTP_X_FLOUCI_SIGNATURE=signature,
    )
    assert second.status_code == 200
    assert WebhookEvent.objects.filter(event_id="evt_0001").count() == 1
    assert client.get("/api/v1/wallet/me/").data["balance"] == Decimal("30.000")

    # Forged signature is refused outright.
    forged = APIClient().post(
        "/api/v1/payments/webhooks/flouci/", body, content_type="application/json",
        HTTP_X_FLOUCI_SIGNATURE="deadbeef",
    )
    assert forged.status_code == 400


@pytest.mark.django_db
def test_driver_cancel_after_payment_refunds_the_passenger():
    driver, vehicle = _make_driver("+21640000007")
    ride = _make_ride(driver, vehicle, seats_total=2)
    passenger = User.objects.create_user(phone_number="+21640000008", password="x")
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)

    _fund_wallet(passenger, "50.000")
    booking = Booking.objects.get(pk=_create_booking(passenger_client, ride, seats=1).data["id"])
    assert _transition(driver_client, booking, "accept").status_code == 200
    assert _pay(passenger_client, booking).status_code == 200
    assert client_balance(passenger) == Decimal("35.000")  # 50 paid, 15 held for the ride

    response = _transition(driver_client, booking, "cancel_driver")
    assert response.status_code == 200
    txn = Transaction.objects.get(reference=f"refund:{booking.id}")
    assert txn.kind == "REFUND"
    assert sum(e.amount for e in txn.entries.all()) == 0
    # Passenger made whole, driver debited back, platform fee returned.
    assert client_balance(passenger) == Decimal("50.000")
    assert client_balance(driver) == 0


@pytest.mark.django_db
def test_wallet_ledger_history_is_scoped_to_the_owner():
    owner = User.objects.create_user(phone_number="+21640000009", password="x")
    other = User.objects.create_user(phone_number="+21640000010", password="x")
    _fund_wallet(owner, "10.000")

    other_client = APIClient()
    other_client.force_authenticate(other)
    response = other_client.get("/api/v1/wallet/entries/")
    assert response.status_code == 200
    assert response.data["results"] == []  # the other wallet sees nothing


def client_balance(user) -> Decimal:
    client = APIClient()
    client.force_authenticate(user)
    return client.get("/api/v1/wallet/me/").data["balance"]


def _pending_flouci_intent(user, amount="30.000", reference="pay_hard_1"):
    return PaymentIntent.objects.create(
        user=user, gateway=PaymentIntent.Gateway.FLOUCI, amount=Decimal(amount), gateway_reference=reference,
    )


def _deliver(event_id, event, reference, amount="30.000", currency=None):
    payment = {"id": reference}
    if amount is not None:
        payment["amount"] = amount
    if currency is not None:
        payment["currency"] = currency
    body = json.dumps({"event_id": event_id, "event": event, "data": {"payment": payment}}).encode()
    signature = hmac.new(settings.FLUOCI_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return APIClient().post(
        "/api/v1/payments/webhooks/flouci/", body, content_type="application/json",
        HTTP_X_FLOUCI_SIGNATURE=signature,
    )


@pytest.mark.django_db
def test_webhook_whose_settlement_fails_is_not_recorded_and_its_retry_credits():
    user = User.objects.create_user(phone_number="+21640000101", password="x")
    intent = _pending_flouci_intent(user)

    with patch.object(payment_services, "post_transaction", side_effect=RuntimeError("db hiccup")):
        with pytest.raises(RuntimeError):
            _deliver("evt_retry", "payment.succeeded", "pay_hard_1")
    assert not WebhookEvent.objects.filter(event_id="evt_retry").exists()
    intent.refresh_from_db()
    assert intent.status == "PENDING"

    assert _deliver("evt_retry", "payment.succeeded", "pay_hard_1").status_code == 200
    assert client_balance(user) == Decimal("30.000")
    assert WebhookEvent.objects.get(event_id="evt_retry").outcome == "credited"


@pytest.mark.django_db
def test_two_distinct_success_events_for_one_payment_credit_once():
    user = User.objects.create_user(phone_number="+21640000102", password="x")
    _pending_flouci_intent(user)
    _deliver("evt_a", "payment.succeeded", "pay_hard_1")
    _deliver("evt_b", "payment.succeeded", "pay_hard_1")
    assert client_balance(user) == Decimal("30.000")
    assert WebhookEvent.objects.get(event_id="evt_b").outcome == "already_paid"


@pytest.mark.django_db
@pytest.mark.parametrize("amount,currency", [("29.000", None), (None, None), ("30.000", "EUR"), ("abc", None)])
def test_success_with_wrong_or_missing_amount_is_not_credited(amount, currency):
    user = User.objects.create_user(phone_number="+21640000103", password="x")
    intent = _pending_flouci_intent(user)
    assert _deliver("evt_mismatch", "payment.succeeded", "pay_hard_1", amount, currency).status_code == 200
    intent.refresh_from_db()
    assert intent.status == "PENDING"
    assert client_balance(user) == 0
    assert WebhookEvent.objects.get(event_id="evt_mismatch").outcome == "amount_mismatch"


@pytest.mark.django_db
def test_unknown_reference_is_recorded_without_crediting_anyone():
    assert _deliver("evt_unknown", "payment.succeeded", "pay_nobody").status_code == 200
    assert WebhookEvent.objects.get(event_id="evt_unknown").outcome == "unknown_intent"
    assert not Transaction.objects.filter(kind="TOPUP").exists()


@pytest.mark.django_db
def test_failure_event_marks_pending_intent_and_a_later_success_still_credits():
    user = User.objects.create_user(phone_number="+21640000104", password="x")
    intent = _pending_flouci_intent(user)
    _deliver("evt_fail", "payment.failed", "pay_hard_1")
    intent.refresh_from_db()
    assert intent.status == "FAILED"
    assert client_balance(user) == 0

    _deliver("evt_late_ok", "payment.succeeded", "pay_hard_1")
    intent.refresh_from_db()
    assert intent.status == "PAID"
    assert client_balance(user) == Decimal("30.000")

    # A failure arriving after payment cannot un-pay it.
    _deliver("evt_late_fail", "payment.cancelled", "pay_hard_1")
    intent.refresh_from_db()
    assert intent.status == "PAID"
    assert WebhookEvent.objects.get(event_id="evt_late_fail").outcome == "ignored_not_pending"


@pytest.mark.django_db
def test_gateway_refusal_at_initiation_keeps_a_failed_intent_and_returns_502():
    user = User.objects.create_user(phone_number="+21640000105", password="x")
    client = APIClient()
    client.force_authenticate(user)
    with patch.object(FlouciGateway, "_config", side_effect=payment_services.GatewayError("Flouci is not configured")):
        response = client.post("/api/v1/payments/topup/", {"amount": "20.000", "gateway": "FLOUCI"}, format="json")
    assert response.status_code == 502
    assert "Nothing was charged" in response.data["detail"]
    assert response.data["intent"]["status"] == "FAILED"
    assert PaymentIntent.objects.get(pk=response.data["intent"]["id"]).status == "FAILED"
    assert client_balance(user) == 0


@pytest.mark.django_db
def test_intents_are_owner_scoped_and_only_pending_ones_cancel():
    owner = User.objects.create_user(phone_number="+21640000106", password="x")
    other = User.objects.create_user(phone_number="+21640000107", password="x")
    intent = _pending_flouci_intent(owner)
    client = APIClient()
    client.force_authenticate(owner)
    stranger = APIClient()
    stranger.force_authenticate(other)

    listing = client.get("/api/v1/payments/intents/")
    assert [row["id"] for row in listing.data["results"]] == [str(intent.id)]
    assert stranger.get("/api/v1/payments/intents/").data["results"] == []
    assert stranger.get(f"/api/v1/payments/intents/{intent.id}/").status_code == 404
    assert stranger.post(f"/api/v1/payments/intents/{intent.id}/cancel/").status_code == 404

    cancelled = client.post(f"/api/v1/payments/intents/{intent.id}/cancel/")
    assert cancelled.status_code == 200
    assert cancelled.data["status"] == "CANCELLED"
    assert client.post(f"/api/v1/payments/intents/{intent.id}/cancel/").status_code == 409
    assert client.get(f"/api/v1/payments/intents/{intent.id}/").data["status"] == "CANCELLED"


@pytest.mark.django_db
def test_history_filters_by_kind_links_bookings_and_earnings_agree_with_the_ledger():
    driver, vehicle = _make_driver("+21640000201")
    ride = _make_ride(driver, vehicle, seats_total=3)
    passenger = User.objects.create_user(phone_number="+21640000202", password="x")
    passenger_client, driver_client = APIClient(), APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client.force_authenticate(driver)
    _fund_wallet(passenger, "50.000")

    kept = Booking.objects.get(pk=_create_booking(passenger_client, ride, seats=2).data["id"])
    _transition(driver_client, kept, "accept")
    _pay(passenger_client, kept)
    other = User.objects.create_user(phone_number="+21640000203", password="x")
    other_client = APIClient()
    other_client.force_authenticate(other)
    _fund_wallet(other, "20.000")
    dropped = Booking.objects.get(pk=_create_booking(other_client, ride, seats=1).data["id"])
    _transition(driver_client, dropped, "accept")
    _pay(other_client, dropped)
    assert _transition(driver_client, dropped, "cancel_driver").status_code == 200

    history = passenger_client.get("/api/v1/wallet/entries/").data["results"]
    assert [row["kind"] for row in history] == ["RIDE_PAYMENT", "TOPUP"]
    assert history[0]["booking"] == str(kept.id) and history[1]["booking"] is None
    only_topups = passenger_client.get("/api/v1/wallet/entries/?kind=topup").data["results"]
    assert [row["kind"] for row in only_topups] == ["TOPUP"]
    driver_rows = driver_client.get("/api/v1/wallet/entries/?kind=RIDE_PAYMENT,REFUND").data["results"]
    assert sorted(row["kind"] for row in driver_rows) == ["REFUND", "RIDE_PAYMENT", "RIDE_PAYMENT"]

    earnings = driver_client.get("/api/v1/wallet/earnings/").data
    # Fares 30 + 15 = 45; fee 10% kept only on the 30 booking; the 15 fare fully refunded.
    assert earnings["gross_fares"] == "45.000"
    assert earnings["platform_fees"] == "3.000"
    assert earnings["refunded"] == "15.000"
    assert earnings["net_earnings"] == "27.000"
    assert earnings["paid_bookings"] == 2 and earnings["refunded_bookings"] == 1
    assert driver_client.get("/api/v1/wallet/me/").data["balance"] == Decimal("27.000")

    assert passenger_client.get("/api/v1/wallet/earnings/").status_code == 403
