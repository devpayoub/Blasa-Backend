from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.bookings.models import Booking
from apps.bookings.state_machine import BookingStatus
from apps.brands.models import Brand, Campaign, SponsoredRide
from apps.incentives.models import Voucher
from apps.moderation.models import Ban
from apps.payments import services as payment_services
from apps.payments.models import PaymentIntent
from apps.rides import services as ride_services
from apps.users.models import User
from apps.vehicles.models import Vehicle
from apps.verification.models import VerificationDocument
from apps.wallet.models import LedgerEntry, Transaction
from apps.wallet.services import balance_of

TUNIS = Point(10.18, 36.80, srid=4326)
SOUSSE = Point(10.64, 35.83, srid=4326)
FARE = Decimal("20.000")
FEE = Decimal("2.000")  # 10% platform fee


def _make_driver(phone: str):
    driver = User.objects.create_user(phone_number=phone, password="x", role=User.Role.DRIVER)
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE, storage_key=f"verification/{phone}.pdf",
        status=VerificationDocument.Status.APPROVED,
    )
    vehicle = Vehicle.objects.create(
        driver=driver, make="Dacia", model="Logan", plate_number=f"{phone[-4:]} TU 7777",
        seats=4, carte_grise=document,
    )
    return driver, vehicle


def _make_ride(driver, vehicle):
    return ride_services.publish_ride(
        driver=driver, vehicle=vehicle,
        origin=TUNIS, destination=SOUSSE,
        origin_label="Tunis", destination_label="Sousse",
        departure_at=timezone.now() + timedelta(days=1),
        seats_total=3, price_per_seat=FARE,
    )


def _make_booking(driver, vehicle, passenger, ride=None):
    ride = ride or _make_ride(driver, vehicle)
    client = APIClient()
    client.force_authenticate(passenger)
    response = client.post(
        "/api/v1/bookings/",
        {"ride": str(ride.id), "seats": 1,
         "pickup_point": {"type": "Point", "coordinates": [10.20, 36.79]},
         "dropoff_point": {"type": "Point", "coordinates": [10.60, 35.85]}},
        format="json",
    )
    assert response.status_code == 201
    return Booking.objects.get(pk=response.data["id"])


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


def _topup(user, amount):
    return payment_services.create_topup(user=user, amount=Decimal(amount), gateway=PaymentIntent.Gateway.CASH)


def _confirmed_booking(driver, vehicle, passenger):
    """A confirmed, fully-funded, unsponsored booking (no discounts)."""
    booking = _make_booking(driver, vehicle, passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    _topup(passenger, FARE)
    assert _transition(driver_client, booking, "accept").status_code == 200
    assert _pay(passenger_client, booking).status_code == 200
    return booking


def _staff_client():
    staff = User.objects.create_user(
        phone_number="+21670000010", password="x", is_staff=True, is_superuser=True
    )
    client = APIClient()
    client.force_authenticate(staff)
    return client, staff


def _legs(kind, reference):
    txn = Transaction.objects.filter(kind=kind, reference=reference).first()
    return txn, {e.account.code: e.amount for e in txn.entries.all()} if txn else None


# ------------------------------------------------------------------- brands


@pytest.mark.django_db
def test_sponsored_ride_discounts_passenger_and_posts_sponsor_leg():
    driver, vehicle = _make_driver("+21670000001")
    passenger = User.objects.create_user(phone_number="+21670000002", password="x")
    ride = _make_ride(driver, vehicle)

    staff_client, staff = _staff_client()
    brand = staff_client.post(
        "/api/v1/brands/brands/", {"name": "Blada Cola", "website": "https://blada.tn"}, format="json"
    )
    assert brand.status_code == 201
    campaign = staff_client.post(
        "/api/v1/brands/campaigns/",
        {"brand": brand.data["id"], "name": "Tunis-Sousse spring", "discount_per_seat": "5.000",
         "budget": "100.000", "is_active": True},
        format="json",
    )
    assert campaign.status_code == 201
    campaign_id = campaign.data["id"]

    # Fund the campaign with sponsor credits, then sponsor the ride.
    funded = staff_client.post(f"/api/v1/brands/campaigns/{campaign_id}/fund/", {"amount": "50.000"}, format="json")
    assert funded.status_code == 201
    sponsored = staff_client.post(
        "/api/v1/brands/sponsored-rides/", {"campaign": campaign_id, "ride": str(ride.id)}, format="json"
    )
    assert sponsored.status_code == 201

    # The passenger tops up only what they expect to pay out of pocket.
    booking = _make_booking(driver, vehicle, passenger, ride=ride)
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    _topup(passenger, FARE - Decimal("5.000"))
    assert _transition(driver_client, booking, "accept").status_code == 200
    confirmed = _pay(passenger_client, booking)
    assert confirmed.status_code == 200

    # Sponsor leg on the ledger, in its own SPONSOR_SUBSIDY transaction.
    _, legs = _legs("SPONSOR_SUBSIDY", f"sponsor_subsidy:{booking.id}")
    sponsor_code = f"sponsor:campaign:{campaign_id}"
    assert legs[sponsor_code] == Decimal("-5.000")
    assert legs[f"user:{passenger.pk}"] == Decimal("5.000")

    # The full ride payment is intact; net passenger cost is fare minus discount.
    _, pay_legs = _legs("RIDE_PAYMENT", f"ride_payment:{booking.id}")
    assert pay_legs[f"user:{passenger.pk}"] == -FARE
    assert pay_legs[f"user:{driver.pk}"] == FARE - FEE
    from apps.wallet.services import get_system_account

    assert balance_of(get_system_account("sys:platform_revenue")) == FEE

    # Campaign bookkeeping moved with the subsidy.
    refreshed = Campaign.objects.get(pk=campaign_id)
    assert refreshed.spent == Decimal("5.000")
    campaign_view = staff_client.get(f"/api/v1/brands/campaigns/{campaign_id}/").data
    assert campaign_view["balance"] == "45.000"


@pytest.mark.django_db
def test_confirm_fails_when_sponsor_pool_is_unfunded():
    driver, vehicle = _make_driver("+21670000003")
    passenger = User.objects.create_user(phone_number="+21670000004", password="x")
    ride = _make_ride(driver, vehicle)
    staff_client, staff_user = _staff_client()
    brand = Brand.objects.create(name="Aerialo", owner=staff_user)
    campaign = Campaign.objects.create(brand=brand, name="x", discount_per_seat=Decimal("5.000"), budget=Decimal("10.000"))
    SponsoredRide.objects.create(campaign=campaign, ride=ride)

    booking = _make_booking(driver, vehicle, passenger, ride=ride)
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    _topup(passenger, FARE - Decimal("5.000"))  # only enough with the discount
    assert _transition(driver_client, booking, "accept").status_code == 200
    refused = _pay(passenger_client, booking)
    assert refused.status_code == 409
    # The transition rolled back: the booking is still ACCEPTED, never confirmed unpaid.
    assert Booking.objects.get(pk=booking.id).status == BookingStatus.ACCEPTED


@pytest.mark.django_db
def test_driver_cancel_reverses_subsidy_and_refunds():
    driver, vehicle = _make_driver("+21670000005")
    passenger = User.objects.create_user(phone_number="+21670000006", password="x")
    ride = _make_ride(driver, vehicle)
    staff_client, staff_user = _staff_client()
    brand = Brand.objects.create(name="CactusAir", owner=staff_user)
    campaign = Campaign.objects.create(brand=brand, name="y", discount_per_seat=Decimal("4.000"), budget=Decimal("40.000"))
    SponsoredRide.objects.create(campaign=campaign, ride=ride)
    staff_client.post(
        f"/api/v1/brands/campaigns/{campaign.id}/fund/", {"amount": "40.000"}, format="json"
    )

    booking = _make_booking(driver, vehicle, passenger, ride=ride)
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    _topup(passenger, FARE - Decimal("4.000"))
    _transition(driver_client, booking, "accept")
    _pay(passenger_client, booking)
    assert _transition(driver_client, booking, "cancel_driver").status_code == 200

    # Subsidy reversed: sponsor account made whole, passenger's discount clawed back.
    _, legs = _legs("SPONSOR_SUBSIDY_REVERSAL", f"sponsor_subsidy_reversal:{booking.id}")
    assert legs[f"sponsor:campaign:{campaign.id}"] == Decimal("4.000")
    _, refund_legs = _legs("REFUND", f"refund:{booking.id}")
    assert refund_legs[f"user:{passenger.pk}"] == FARE

    # Net position: the passenger is made whole (balance = what they topped up),
    # the sponsor account is back to its funding, and the campaign spend resets.
    passenger_net = sum(
        e.amount for e in LedgerEntry.objects.filter(account__code=f"user:{passenger.pk}")
    )
    assert passenger_net == FARE - Decimal("4.000")
    sponsor_net = sum(
        e.amount for e in LedgerEntry.objects.filter(account__code=f"sponsor:campaign:{campaign.id}")
    )
    assert sponsor_net == Decimal("40.000")
    assert Campaign.objects.get(pk=campaign.id).spent == 0


# ---------------------------------------------------------------- incentives


@pytest.mark.django_db
def test_voucher_reduces_cash_needed_at_confirm_and_is_single_use():
    driver, vehicle = _make_driver("+21670000007")
    passenger = User.objects.create_user(phone_number="+21670000008", password="x")
    staff_client, _ = _staff_client()

    issued = staff_client.post(
        "/api/v1/incentives/vouchers/", {"user": passenger.pk, "amount": "6.000"}, format="json"
    )
    assert issued.status_code == 201
    assert Voucher.objects.filter(code=issued.data["code"], issued_to=passenger).exists()

    # The passenger can see it and tops up only the remainder.
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    listing = passenger_client.get("/api/v1/incentives/vouchers/")
    assert [v["code"] for v in listing.data["results"]] == [issued.data["code"]]

    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    booking = _make_booking(driver, vehicle, passenger)
    _topup(passenger, FARE - Decimal("6.000"))
    _transition(driver_client, booking, "accept")
    assert _pay(passenger_client, booking).status_code == 200

    _, legs = _legs("VOUCHER_REDEEM", f"voucher_redeem:{booking.id}")
    assert legs["sys:incentive_pool"] == Decimal("-6.000")
    assert legs[f"user:{passenger.pk}"] == Decimal("6.000")
    assert not passenger_client.get("/api/v1/incentives/vouchers/").data["results"]

    # A second confirmed booking does not consume the spent voucher again.
    booking2 = _make_booking(driver, vehicle, passenger)
    _topup(passenger, FARE)
    _transition(driver_client, booking2, "accept")
    assert _pay(passenger_client, booking2).status_code == 200
    assert not Transaction.objects.filter(kind="VOUCHER_REDEEM", reference=f"voucher_redeem:{booking2.id}").exists()


@pytest.mark.django_db
def test_expired_voucher_is_not_redeemed():
    driver, vehicle = _make_driver("+21670000009")
    passenger = User.objects.create_user(phone_number="+21670000011", password="x")
    staff_client, _ = _staff_client()
    staff_client.post(
        "/api/v1/incentives/vouchers/",
        {"user": passenger.pk, "amount": "6.000", "expires_at": (timezone.now() - timedelta(days=1)).isoformat()},
        format="json",
    )
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    assert not passenger_client.get("/api/v1/incentives/vouchers/").data["results"]

    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    booking = _make_booking(driver, vehicle, passenger)
    _topup(passenger, FARE - Decimal("6.000"))  # not enough without a voucher
    _transition(driver_client, booking, "accept")
    assert _pay(passenger_client, booking).status_code == 409


# ---------------------------------------------------------------- moderation


@pytest.mark.django_db
def test_report_lifecycle_staff_queue_and_duplicate_guard():
    driver, _ = _make_driver("+21670000012")
    passenger = User.objects.create_user(phone_number="+21670000013", password="x")
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)

    filed = passenger_client.post(
        "/api/v1/moderation/reports/",
        {"target_type": "USER", "target_id": str(driver.id), "reason": "FRAUD", "details": "asked cash off-platform"},
        format="json",
    )
    assert filed.status_code == 201
    assert filed.data["status"] == "OPEN"

    # One open report per target per reporter.
    duplicate = passenger_client.post(
        "/api/v1/moderation/reports/",
        {"target_type": "USER", "target_id": str(driver.id), "reason": "SPAM"},
        format="json",
    )
    assert duplicate.status_code == 400

    staff_client, staff = _staff_client()
    queue = staff_client.get("/api/v1/moderation/reports/")
    assert queue.status_code == 200
    assert len(queue.data["results"]) == 1

    reviewed = staff_client.post(
        f"/api/v1/moderation/reports/{filed.data['id']}/review/", {"status": "ACTIONED"}, format="json"
    )
    assert reviewed.data["status"] == "ACTIONED"
    assert reviewed.data["handled_at"] is not None
    # Already-handled reports cannot be reviewed twice.
    assert staff_client.post(
        f"/api/v1/moderation/reports/{filed.data['id']}/review/", {"status": "DISMISSED"}, format="json"
    ).status_code == 400

    # Regular users cannot see the moderation queue.
    assert passenger_client.get("/api/v1/moderation/reports/").status_code == 403


@pytest.mark.django_db
def test_ban_deactivates_the_account_until_unban():
    driver, _ = _make_driver("+21670000014")
    User.objects.create_user(phone_number="+21670000015", password="x")
    staff_client, _ = _staff_client()

    def jwt_client(user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
        return client

    assert jwt_client(driver).get("/api/v1/rides/").status_code == 200

    banned = staff_client.post(
        f"/api/v1/moderation/users/{driver.id}/ban/", {"reason": "repeat no-shows"}, format="json"
    )
    assert banned.status_code == 201
    assert Ban.objects.filter(user=driver, revoked_at__isnull=True).exists()

    # The ban is immediate: the same token stops working (401 inactive user).
    assert jwt_client(driver).get("/api/v1/rides/").status_code == 401

    # Double bans are refused; unban restores access on the existing token.
    assert staff_client.post(
        f"/api/v1/moderation/users/{driver.id}/ban/", {"reason": "again"}, format="json"
    ).status_code == 400
    assert staff_client.delete(f"/api/v1/moderation/users/{driver.id}/ban/").status_code == 204
    assert jwt_client(driver).get("/api/v1/rides/").status_code == 200


# ---------------------------------------------------------------- reporting


@pytest.mark.django_db
def test_reporting_admin_only_revenue_and_export():
    driver, vehicle = _make_driver("+21670000016")
    passenger = User.objects.create_user(phone_number="+21670000017", password="x")
    first = _confirmed_booking(driver, vehicle, passenger)

    # Regular users are refused everywhere in reporting.
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    assert passenger_client.get("/api/v1/reporting/revenue/").status_code == 403

    staff_client, _ = _staff_client()
    revenue = staff_client.get("/api/v1/reporting/revenue/")
    assert revenue.status_code == 200
    # One confirmed booking so far -> one platform fee posted.
    assert revenue.data["platform_revenue"] == FEE
    assert revenue.data["sponsor_subsidies"] == 0

    rides = staff_client.get("/api/v1/reporting/rides/")
    assert rides.status_code == 200
    assert rides.data["total_rides"] >= 1

    cancelled = _confirmed_booking(driver, vehicle, passenger)
    first_driver_client = APIClient()
    first_driver_client.force_authenticate(driver)
    assert _start(first_driver_client, first).status_code == 200
    assert _transition(first_driver_client, first, "complete").status_code == 200
    passenger_client2 = APIClient()
    passenger_client2.force_authenticate(passenger)
    assert _transition(passenger_client2, cancelled, "cancel_passenger").status_code == 200

    # Two settled payments -> two gross platform fees; the passenger cancelled before
    # departure, so that fee is reversed by exactly one REFUND posting.
    summary = staff_client.get("/api/v1/reporting/revenue/").data
    assert summary["platform_revenue"] == FEE * 2
    assert summary["revenue_by_kind"]["REFUND"] == -FEE
    assert summary["refunds"] == 1

    cancellations = staff_client.get("/api/v1/reporting/cancellations/")
    assert cancellations.data["total_bookings"] == 2
    assert cancellations.data["completed"] == 1
    assert cancellations.data["cancelled_by_passenger"] == 1
    assert cancellations.data["cancellation_rate"] == 0.5

    export = staff_client.get("/api/v1/reporting/export/", {"dataset": "ledger"})
    assert export.status_code == 200
    assert "text/csv" in export["Content-Type"]
    assert "RIDE_PAYMENT" in export.content.decode()

    assert staff_client.get("/api/v1/reporting/export/", {"dataset": "nope"}).status_code == 400
