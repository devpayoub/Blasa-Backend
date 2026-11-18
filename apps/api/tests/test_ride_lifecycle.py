import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.db import connection
from django.db.models import Sum
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bookings.models import Booking
from apps.bookings.state_machine import BookingStatus
from apps.brands import services as brands_services
from apps.brands.models import Brand, Campaign, SponsoredRide
from apps.incentives import services as incentives_services
from apps.incentives.models import Voucher
from apps.payments import services as payment_services
from apps.payments.models import PaymentIntent
from apps.rides import services as ride_services
from apps.rides.models import Ride
from apps.users.models import User
from apps.vehicles.models import Vehicle
from apps.verification.models import VerificationDocument
from apps.wallet.models import Account, LedgerEntry, Transaction
from apps.wallet.services import balance_of, ensure_user_account, get_system_account, post_transaction

TUNIS = Point(10.18, 36.80, srid=4326)
SOUSSE = Point(10.64, 35.83, srid=4326)
FARE = Decimal("15.000")
FEE = Decimal("1.500")


def _driver(phone, seats=4):
    driver = User.objects.create_user(phone_number=phone, password="x", role=User.Role.DRIVER)
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE,
        storage_key=f"verification/{phone}.pdf", status=VerificationDocument.Status.APPROVED,
    )
    vehicle = Vehicle.objects.create(
        driver=driver, make="Synthetic", model="Sedan", plate_number=f"{phone[-4:]} TU 9", seats=seats,
        carte_grise=document,
    )
    return driver, vehicle


def _ride(driver, vehicle, seats_total=3):
    return ride_services.publish_ride(
        driver=driver, vehicle=vehicle, origin=TUNIS, destination=SOUSSE,
        origin_label="Tunis", destination_label="Sousse",
        departure_at=timezone.now() + timedelta(days=1), seats_total=seats_total, price_per_seat=FARE,
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _book(passenger, ride, seats=1):
    response = _client(passenger).post(
        "/api/v1/bookings/",
        {"ride": str(ride.id), "seats": seats,
         "pickup_point": {"type": "Point", "coordinates": [10.20, 36.79]},
         "dropoff_point": {"type": "Point", "coordinates": [10.60, 35.85]}},
        format="json",
    )
    assert response.status_code == 201, response.data
    return Booking.objects.get(pk=response.data["id"])


def _act(user, booking, action, **body):
    return _client(user).post(f"/api/v1/bookings/{booking.id}/transitions/{action}/", body, format="json")


def _pay(passenger, booking):
    quote = _client(passenger).get(f"/api/v1/bookings/{booking.id}/payment-quote/")
    assert quote.status_code == 200, quote.data
    return _act(passenger, booking, "confirm", expected_out_of_pocket=str(quote.data["out_of_pocket"]))


def _topup(user, amount):
    payment_services.create_topup(user=user, amount=Decimal(amount), gateway=PaymentIntent.Gateway.CASH)


def _wallet(user):
    return balance_of(ensure_user_account(user))


def _passenger(phone, funds="0"):
    user = User.objects.create_user(phone_number=phone, password="x")
    if Decimal(funds):
        _topup(user, funds)
    return user


def _sponsor(ride, *, discount="4.000", fund="40.000"):
    owner = User.objects.create_user(phone_number="+21679990000", password="x", is_staff=True)
    brand = Brand.objects.create(name="Synthetic Sponsor", owner=owner)
    campaign = Campaign.objects.create(
        brand=brand, name="Synthetic campaign", discount_per_seat=Decimal(discount), budget=Decimal("100.000"),
    )
    brands_services.fund_campaign(campaign=campaign, amount=Decimal(fund), reference=f"fund:{campaign.pk}")
    SponsoredRide.objects.create(campaign=campaign, ride=ride)
    return campaign


def _ledger_balances():
    assert LedgerEntry.objects.aggregate(total=Sum("amount"))["total"] in (None, Decimal("0"))
    for txn in Transaction.objects.all():
        assert sum(e.amount for e in txn.entries.all()) == 0, txn.reference


# ----------------------------------------------------------------- payment consent


@pytest.mark.django_db
def test_passenger_previews_and_authorizes_payment_driver_cannot_charge():
    driver, vehicle = _driver("+21679000001")
    ride = _ride(driver, vehicle)
    campaign = _sponsor(ride, discount="10.000")
    passenger = _passenger("+21679000002", funds="1.000")
    voucher = incentives_services.issue_voucher(user=passenger, amount=Decimal("8.000"))
    booking = _book(passenger, ride)
    assert _act(driver, booking, "accept").status_code == 200

    quote = _client(passenger).get(f"/api/v1/bookings/{booking.id}/payment-quote/").data
    # Voucher is capped at what the subsidy leaves to pay, never over-crediting the passenger.
    assert Decimal(quote["fare"]) == FARE and Decimal(quote["subsidy"]) == Decimal("10.000")
    assert Decimal(quote["voucher"]) == Decimal("5.000") and quote["voucher_code"] == voucher.code
    assert Decimal(quote["out_of_pocket"]) == Decimal("0.000") and quote["sufficient"] is True

    # The driver may neither see the passenger's quote nor confirm the charge.
    assert _client(driver).get(f"/api/v1/bookings/{booking.id}/payment-quote/").status_code == 403
    assert _act(driver, booking, "confirm", expected_out_of_pocket="0.000").status_code == 403
    # Missing or stale approval is refused without posting anything.
    assert _act(passenger, booking, "confirm").status_code == 409
    stale = _act(passenger, booking, "confirm", expected_out_of_pocket="2.000")
    assert stale.status_code == 409 and "changed" in stale.data["detail"]
    assert not Transaction.objects.filter(reference__endswith=str(booking.id)).exists()
    assert Campaign.objects.get(pk=campaign.pk).spent == 0

    confirmed = _pay(passenger, booking)
    assert confirmed.status_code == 200
    assert confirmed.data["paid_amount"] == "0.000"
    assert confirmed.data["subsidy_amount"] == "10.000" and confirmed.data["voucher_amount"] == "5.000"
    assert _wallet(passenger) == Decimal("1.000")
    assert Voucher.objects.get(pk=voucher.pk).redeemed_by_id == booking.id
    _ledger_balances()


@pytest.mark.django_db
def test_insufficient_balance_quote_and_confirm_rolls_back():
    driver, vehicle = _driver("+21679000003")
    ride = _ride(driver, vehicle)
    passenger = _passenger("+21679000004", funds="5.000")
    booking = _book(passenger, ride)
    _act(driver, booking, "accept")
    quote = _client(passenger).get(f"/api/v1/bookings/{booking.id}/payment-quote/").data
    assert quote["sufficient"] is False and Decimal(quote["balance"]) == Decimal("5.000")
    refused = _pay(passenger, booking)
    assert refused.status_code == 409 and "insufficient" in refused.data["detail"].lower()
    booking.refresh_from_db()
    assert booking.status == BookingStatus.ACCEPTED and booking.paid_amount is None


# ----------------------------------------------------------------- cancellation


@pytest.mark.django_db
def test_passenger_cancellation_preview_matches_refund_applied_once():
    driver, vehicle = _driver("+21679000005")
    ride = _ride(driver, vehicle, seats_total=2)
    passenger = _passenger("+21679000006", funds="20.000")
    booking = _book(passenger, ride, seats=1)
    _act(driver, booking, "accept")
    assert _pay(passenger, booking).status_code == 200
    assert _wallet(passenger) == Decimal("5.000")

    preview = _client(passenger).get(f"/api/v1/bookings/{booking.id}/cancellation-quote/").data
    assert preview["allowed"] is True and preview["action"] == "cancel_passenger"
    assert Decimal(preview["refund"]) == FARE and preview["seats_released"] == 1

    cancelled = _act(passenger, booking, "cancel_passenger")
    assert cancelled.status_code == 200 and cancelled.data["refunded_amount"] == "15.000"
    assert _wallet(passenger) == Decimal("20.000")
    assert _wallet(driver) == 0
    assert Ride.objects.get(pk=ride.pk).seats_available == 2

    # A retry is an invalid transition and posts nothing more.
    assert _act(passenger, booking, "cancel_passenger").status_code == 409
    assert Transaction.objects.filter(kind="REFUND").count() == 1
    after = _client(passenger).get(f"/api/v1/bookings/{booking.id}/cancellation-quote/").data
    assert after["allowed"] is False
    _ledger_balances()


@pytest.mark.django_db
def test_ride_cancellation_terminates_every_booking_and_refunds_original_legs_once():
    driver, vehicle = _driver("+21679000007")
    ride = _ride(driver, vehicle, seats_total=3)
    campaign = _sponsor(ride, discount="4.000")
    paid = _passenger("+21679000008", funds="11.000")
    voucher = incentives_services.issue_voucher(user=paid, amount=Decimal("3.000"))
    held = _passenger("+21679000009")
    pending = _passenger("+21679000010")

    paid_booking = _book(paid, ride)
    held_booking = _book(held, ride)
    pending_booking = _book(pending, ride)
    assert pending_booking.status == BookingStatus.REQUESTED
    _act(driver, paid_booking, "accept")
    _act(driver, held_booking, "accept")
    assert _pay(paid, paid_booking).status_code == 200
    assert _wallet(paid) == Decimal("3.000")  # 15 fare - 4 subsidy - 3 voucher = 8 out of pocket
    assert Campaign.objects.get(pk=campaign.pk).spent == Decimal("4.000")

    # The campaign ends before cancellation: the reversal still targets the account that paid.
    Campaign.objects.filter(pk=campaign.pk).update(is_active=False)

    preview = _client(driver).get(f"/api/v1/rides/{ride.id}/cancellation-quote/").data
    assert preview["allowed"] is True and preview["bookings_cancelled"] == 3
    assert preview["paid_bookings"] == 1 and Decimal(preview["total_refund"]) == Decimal("8.000")

    # Someone else's driver can neither preview nor cancel.
    stranger, _ = _driver("+21679000011")
    assert _client(stranger).get(f"/api/v1/rides/{ride.id}/cancellation-quote/").status_code == 403
    assert _client(stranger).post(f"/api/v1/rides/{ride.id}/cancel/").status_code == 403

    response = _client(driver).post(f"/api/v1/rides/{ride.id}/cancel/")
    assert response.status_code == 200 and response.data["status"] == "CANCELLED"
    statuses = set(Booking.objects.filter(ride=ride).values_list("status", flat=True))
    assert statuses == {BookingStatus.CANCELLED_BY_DRIVER}
    ride.refresh_from_db()
    assert ride.status == Ride.Status.CANCELLED and ride.seats_available == 3 and ride.cancelled_at

    assert _wallet(paid) == Decimal("11.000")
    assert _wallet(driver) == 0
    assert Campaign.objects.get(pk=campaign.pk).spent == 0
    assert brands_services.campaign_spent_total(campaign) == 0
    assert Voucher.objects.get(pk=voucher.pk).redeemed_at is None
    assert Booking.objects.get(pk=paid_booking.pk).refunded_amount == Decimal("8.000")

    # Retrying every reversal changes nothing: counters and postings stay put.
    assert _client(driver).post(f"/api/v1/rides/{ride.id}/cancel/").status_code == 409
    brands_services.reverse_subsidy_for_booking(paid_booking)
    incentives_services.reverse_voucher_for_booking(paid_booking)
    payment_services.refund_booking_payment(booking=paid_booking)
    assert Campaign.objects.get(pk=campaign.pk).spent == 0
    for kind in ("REFUND", "SPONSOR_SUBSIDY_REVERSAL", "VOUCHER_REDEEM_REVERSAL"):
        assert Transaction.objects.filter(kind=kind).count() == 1
    # A cancelled ride is never reopened by seat releases.
    ride_services.release_seats(ride_id=ride.pk, seats=0)
    assert Ride.objects.get(pk=ride.pk).status == Ride.Status.CANCELLED
    _ledger_balances()


@pytest.mark.django_db
def test_refund_after_driver_spent_the_fare_records_debt_without_opening_overdraft():
    driver, vehicle = _driver("+21679000012")
    ride = _ride(driver, vehicle)
    passenger = _passenger("+21679000013", funds="15.000")
    booking = _book(passenger, ride)
    _act(driver, booking, "accept")
    _pay(passenger, booking)
    driver_account = ensure_user_account(driver)
    post_transaction(
        kind="WITHDRAWAL", reference="synthetic:withdraw",
        legs=[(driver_account, -(FARE - FEE)), (get_system_account("sys:gateway_clearing"), FARE - FEE)],
    )

    assert _act(driver, booking, "cancel_driver").status_code == 200
    assert _wallet(passenger) == FARE
    assert _wallet(driver) == -(FARE - FEE)
    assert Account.objects.get(pk=driver_account.pk).allow_negative is False
    _ledger_balances()


@pytest.mark.django_db
def test_subsidy_retry_does_not_double_count_campaign_spend():
    driver, vehicle = _driver("+21679000014")
    ride = _ride(driver, vehicle)
    campaign = _sponsor(ride)
    passenger = _passenger("+21679000015", funds="15.000")
    booking = _book(passenger, ride)
    _act(driver, booking, "accept")
    _pay(passenger, booking)
    assert brands_services.subsidy_for_booking(booking) == Decimal("4.000")
    assert Campaign.objects.get(pk=campaign.pk).spent == Decimal("4.000")
    assert brands_services.campaign_spent_total(campaign) == Decimal("4.000")


# ----------------------------------------------------------------- lifecycle


@pytest.mark.django_db
def test_start_complete_lifecycle_unlocks_ratings_and_blocks_late_actions():
    driver, vehicle = _driver("+21679000016")
    ride = _ride(driver, vehicle, seats_total=3)
    rider = _passenger("+21679000017", funds="15.000")
    absent = _passenger("+21679000018", funds="15.000")
    late = _passenger("+21679000019")
    rider_booking, absent_booking, late_booking = _book(rider, ride), _book(absent, ride), _book(late, ride)
    for booking, passenger in ((rider_booking, rider), (absent_booking, absent)):
        _act(driver, booking, "accept")
        assert _pay(passenger, booking).status_code == 200

    driver_client = _client(driver)
    # Completing or no-showing before departure is refused.
    assert driver_client.post(f"/api/v1/rides/{ride.id}/complete/").status_code == 409
    assert _act(driver, rider_booking, "complete").status_code == 409
    # Unresolved requests block the start; resolving them unblocks it.
    blocked = driver_client.post(f"/api/v1/rides/{ride.id}/start/")
    assert blocked.status_code == 409 and "resolve" in blocked.data["detail"]
    assert _act(driver, late_booking, "reject").status_code == 200

    started = driver_client.post(f"/api/v1/rides/{ride.id}/start/")
    assert started.status_code == 200 and started.data["status"] == "IN_PROGRESS" and started.data["started_at"]
    # Booking admission and cancellation close once the ride is under way.
    assert _client(late).post(
        "/api/v1/bookings/",
        {"ride": str(ride.id), "seats": 1, "pickup_point": {"type": "Point", "coordinates": [10.2, 36.8]},
         "dropoff_point": {"type": "Point", "coordinates": [10.6, 35.8]}}, format="json",
    ).status_code == 400
    assert _act(rider, rider_booking, "cancel_passenger").status_code == 409
    assert driver_client.post(f"/api/v1/rides/{ride.id}/cancel/").status_code == 409
    assert driver_client.patch(f"/api/v1/rides/{ride.id}/", {"notes": "late"}, format="json").status_code == 409

    assert _act(driver, absent_booking, "no_show").status_code == 200
    completed = driver_client.post(f"/api/v1/rides/{ride.id}/complete/")
    assert completed.status_code == 200 and completed.data["status"] == "COMPLETED"
    assert Booking.objects.get(pk=rider_booking.pk).status == BookingStatus.COMPLETED
    assert Booking.objects.get(pk=absent_booking.pk).status == BookingStatus.NO_SHOW
    # No-show is not refunded; settlement timing is unchanged.
    assert not Transaction.objects.filter(kind="REFUND").exists()

    rating = _client(rider).post("/api/v1/ratings/", {"booking": str(rider_booking.id), "score": 5}, format="json")
    assert rating.status_code == 201
    assert driver_client.post(f"/api/v1/rides/{ride.id}/complete/").status_code == 409
    _ledger_balances()


# ----------------------------------------------------------------- editing and scope


@pytest.mark.django_db
def test_owner_edit_policy_capacity_and_foreign_owner():
    driver, vehicle = _driver("+21679000020", seats=3)
    with pytest.raises(ride_services.RideError):
        _ride(driver, vehicle, seats_total=4)
    ride = _ride(driver, vehicle, seats_total=2)
    owner = _client(driver)

    stranger, _ = _driver("+21679000021")
    assert _client(stranger).patch(f"/api/v1/rides/{ride.id}/", {"notes": "x"}, format="json").status_code == 403
    assert _client(stranger).post(f"/api/v1/rides/{ride.id}/start/").status_code == 403

    # Without bookings, every term is editable within the vehicle's capacity.
    edited = owner.patch(f"/api/v1/rides/{ride.id}/", {"price_per_seat": "12.000", "seats_total": 3}, format="json")
    assert edited.status_code == 200 and edited.data["seats_available"] == 3
    assert owner.patch(f"/api/v1/rides/{ride.id}/", {"seats_total": 4}, format="json").status_code == 409
    long_notes = owner.patch(f"/api/v1/rides/{ride.id}/", {"notes": "x" * 501}, format="json")
    assert long_notes.status_code == 400

    passenger = _passenger("+21679000022")
    booking = _book(passenger, ride, seats=2)
    _act(driver, booking, "accept")
    assert Booking.objects.get(pk=booking.pk).fare_amount == Decimal("24.000")

    notes = owner.patch(f"/api/v1/rides/{ride.id}/", {"notes": "Meeting at the Lac 2 entrance."}, format="json")
    assert notes.status_code == 200 and notes.data["notes"] == "Meeting at the Lac 2 entrance."
    price = owner.patch(f"/api/v1/rides/{ride.id}/", {"price_per_seat": "30.000"}, format="json")
    assert price.status_code == 409 and "The price can't change" in price.data["detail"]
    assert owner.patch(f"/api/v1/rides/{ride.id}/", {"seats_total": 1}, format="json").status_code == 409
    # Seats can shrink to exactly what is held, which fills the ride.
    full = owner.patch(f"/api/v1/rides/{ride.id}/", {"seats_total": 2}, format="json")
    assert full.status_code == 200 and full.data["status"] == "FULL"
    assert Booking.objects.get(pk=booking.pk).fare_amount == Decimal("24.000")


@pytest.mark.django_db
def test_ride_list_scopes_and_history_visibility():
    driver, vehicle = _driver("+21679000023")
    upcoming = _ride(driver, vehicle)
    finished = _ride(driver, vehicle)
    _client(driver).post(f"/api/v1/rides/{finished.id}/cancel/")
    passenger = _passenger("+21679000024")
    stranger = _passenger("+21679000025")
    _book(passenger, upcoming)

    bookable = {row["id"] for row in _client(stranger).get("/api/v1/rides/").data["results"]}
    assert str(upcoming.id) in bookable and str(finished.id) not in bookable
    mine = {row["id"] for row in _client(driver).get("/api/v1/rides/", {"scope": "mine"}).data["results"]}
    assert mine == {str(upcoming.id), str(finished.id)}
    joined = {row["id"] for row in _client(passenger).get("/api/v1/rides/", {"scope": "joined"}).data["results"]}
    assert joined == {str(upcoming.id)}

    assert _client(stranger).get(f"/api/v1/rides/{finished.id}/").status_code == 404
    assert _client(driver).get(f"/api/v1/rides/{finished.id}/").status_code == 200


# ----------------------------------------------------------------- concurrency


@pytest.mark.django_db(transaction=True)
def test_concurrent_acceptance_never_oversells():
    driver, vehicle = _driver("+21679000026")
    ride = _ride(driver, vehicle, seats_total=1)
    bookings = [_book(_passenger(f"+2167900003{i}"), ride) for i in range(4)]
    barrier = threading.Barrier(len(bookings))
    codes = []

    def accept(booking):
        try:
            barrier.wait()
            codes.append(_act(driver, booking, "accept").status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=accept, args=(b,)) for b in bookings]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(codes) == [200, 409, 409, 409]
    ride.refresh_from_db()
    assert ride.seats_available == 0 and ride.status == Ride.Status.FULL
    assert Booking.objects.filter(ride=ride, status=BookingStatus.ACCEPTED).count() == 1
