from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bookings import services as booking_services
from apps.incentives import services as incentive_services
from apps.incentives.models import IncentiveAward, IncentiveCampaign, Voucher
from apps.payments import services as payment_services
from apps.payments.models import PaymentIntent
from apps.rides import services as ride_services
from apps.users import services as user_services
from apps.users.models import User
from apps.vehicles import services as vehicle_services
from apps.verification import services as verification_services
from apps.verification.models import VerificationDocument, VerificationReview
from apps.wallet.services import balance_of, ensure_user_account, get_system_account

PICKUP = Point(10.18, 36.80, srid=4326)
DROPOFF = Point(10.64, 35.83, srid=4326)


@pytest.fixture
def staff():
    return User.objects.create_user(phone_number="+21655000001", password="x", is_staff=True)


def _campaign(staff, kind, *, fund="100.000", amount="10.000", **extra):
    campaign = IncentiveCampaign.objects.create(
        name=f"{kind} test", kind=kind, amount=Decimal(amount), is_active=True, **extra
    )
    if fund:
        incentive_services.fund_incentive_campaign(campaign=campaign, amount=Decimal(fund))
    return campaign


def _approve(user, doc_type, staff):
    document = VerificationDocument.objects.create(
        user=user, doc_type=doc_type, storage_key=f"verification/{user.pk}/{doc_type}.pdf",
        uploaded_at=timezone.now(),
    )
    with patch.object(verification_services, "validate_stored_document"):
        verification_services.review_document(
            document=document, reviewer=staff, decision=VerificationReview.Decision.APPROVE,
        )
    return document


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.mark.django_db
def test_welcome_credit_is_granted_once_on_identity_approval_and_reserves_budget(staff):
    campaign = _campaign(staff, IncentiveCampaign.Kind.WELCOME_CREDIT)
    passenger = User.objects.create_user(phone_number="+21655000002", password="x")
    _approve(passenger, VerificationDocument.DocType.CIN, staff)

    award = IncentiveAward.objects.get(user=passenger)
    assert award.kind == "WELCOME_CREDIT" and award.amount == Decimal("10.000")
    assert award.voucher.issued_to == passenger
    assert incentive_services.campaign_balance(campaign) == Decimal("90.000")
    # The credit is a booking voucher, never withdrawable wallet cash.
    assert balance_of(ensure_user_account(passenger)) == 0

    # Re-approval (a replacement CIN) and explicit re-evaluation grant nothing more.
    _approve(passenger, VerificationDocument.DocType.CIN, staff)
    assert incentive_services.evaluate_user(passenger.pk) == []
    assert IncentiveAward.objects.filter(user=passenger).count() == 1
    assert incentive_services.campaign_balance(campaign) == Decimal("90.000")

    rewards = _client(passenger).get("/api/v1/incentives/rewards/").data["results"]
    assert rewards[0]["status"] == "ISSUED" and rewards[0]["kind"] == "WELCOME_CREDIT"
    vouchers = _client(passenger).get("/api/v1/incentives/vouchers/").data["results"]
    assert vouchers[0]["code"] == award.code


@pytest.mark.django_db
def test_no_award_without_live_funded_campaign_and_review_still_succeeds(staff):
    _campaign(staff, IncentiveCampaign.Kind.WELCOME_CREDIT, fund="5.000")  # less than one award
    poor = User.objects.create_user(phone_number="+21655000003", password="x")
    _approve(poor, VerificationDocument.DocType.CIN, staff)
    poor.refresh_from_db()
    assert poor.is_verified  # the review itself committed
    assert not IncentiveAward.objects.filter(user=poor).exists()

    IncentiveCampaign.objects.update(is_active=False)
    nobody = User.objects.create_user(phone_number="+21655000004", password="x")
    _approve(nobody, VerificationDocument.DocType.CIN, staff)
    assert not IncentiveAward.objects.filter(user=nobody).exists()


@pytest.mark.django_db
def test_welcome_voucher_pays_part_of_the_first_booking(staff):
    _campaign(staff, IncentiveCampaign.Kind.WELCOME_CREDIT)
    driver = User.objects.create_user(phone_number="+21655000005", password="x", role=User.Role.DRIVER)
    grey = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE, storage_key="verification/g.pdf",
        status=VerificationDocument.Status.APPROVED,
    )
    vehicle = vehicle_services.register_vehicle(
        driver=driver, make="Kia", model="Rio", plate_number="55 TU 0005", seats=4, carte_grise=grey,
    )
    ride = ride_services.publish_ride(
        driver=driver, vehicle=vehicle, origin=PICKUP, destination=DROPOFF, origin_label="Tunis",
        destination_label="Sousse", departure_at=timezone.now() + timezone.timedelta(days=1),
        seats_total=2, price_per_seat=Decimal("15.000"),
    )
    passenger = User.objects.create_user(phone_number="+21655000006", password="x")
    _approve(passenger, VerificationDocument.DocType.CIN, staff)
    payment_services.create_topup(user=passenger, amount=Decimal("5.000"), gateway=PaymentIntent.Gateway.CASH)

    booking = booking_services.create_booking(
        passenger=passenger, ride=ride, seats=1, pickup_point=PICKUP, dropoff_point=DROPOFF,
    )
    booking_services.apply_action(booking=booking, actor=driver, action="accept")
    booking_services.apply_action(booking=booking, actor=passenger, action="confirm", expected_out_of_pocket="5.000")
    assert balance_of(ensure_user_account(passenger)) == 0  # 5 cash + 10 welcome = 15 fare
    award = IncentiveAward.objects.get(user=passenger)
    assert incentive_services.award_status(award) == "REDEEMED"


@pytest.mark.django_db
def test_fuel_reward_needs_identity_and_an_approved_active_vehicle(staff):
    fuel = _campaign(staff, IncentiveCampaign.Kind.DRIVER_FUEL, amount="20.000")
    _campaign(staff, IncentiveCampaign.Kind.WELCOME_CREDIT)
    user = User.objects.create_user(phone_number="+21655000007", password="x")
    _approve(user, VerificationDocument.DocType.CIN, staff)
    assert list(IncentiveAward.objects.filter(user=user).values_list("kind", flat=True)) == ["WELCOME_CREDIT"]

    user_services.upgrade_to_driver(user)
    assert not IncentiveAward.objects.filter(user=user, kind="DRIVER_FUEL").exists()  # no vehicle yet

    vehicle = vehicle_services.register_vehicle(driver=user, make="Kia", model="Rio", plate_number="55 TU 0007", seats=4)
    assert not IncentiveAward.objects.filter(user=user, kind="DRIVER_FUEL").exists()  # no carte grise yet

    grey = _approve(user, VerificationDocument.DocType.CARTE_GRISE, staff)
    assert not IncentiveAward.objects.filter(user=user, kind="DRIVER_FUEL").exists()  # not linked to vehicle
    vehicle_services.attach_carte_grise(vehicle=vehicle, document=grey)

    award = IncentiveAward.objects.get(user=user, kind="DRIVER_FUEL")
    assert award.code.startswith("FUEL-") and award.voucher is None
    assert incentive_services.campaign_balance(fuel) == Decimal("80.000")
    # Upgrading kept the welcome credit; the user now holds one of each, never two of either.
    assert IncentiveAward.objects.filter(user=user).count() == 2


@pytest.mark.django_db
def test_fuel_code_redeems_exactly_once_and_only_by_staff(staff):
    _campaign(staff, IncentiveCampaign.Kind.DRIVER_FUEL, amount="20.000")
    driver = User.objects.create_user(phone_number="+21655000008", password="x", role=User.Role.DRIVER)
    _approve(driver, VerificationDocument.DocType.CIN, staff)
    grey = _approve(driver, VerificationDocument.DocType.CARTE_GRISE, staff)
    vehicle_services.register_vehicle(
        driver=driver, make="Kia", model="Rio", plate_number="55 TU 0008", seats=4, carte_grise=grey,
    )
    award = IncentiveAward.objects.get(user=driver, kind="DRIVER_FUEL")

    denied = _client(driver).post("/api/v1/incentives/fuel/redeem/", {"code": award.code}, format="json")
    assert denied.status_code == 403

    first = _client(staff).post(
        "/api/v1/incentives/fuel/redeem/", {"code": award.code.lower(), "note": "Station Lac 2"}, format="json",
    )
    assert first.status_code == 200 and first.data["status"] == "REDEEMED"
    second = _client(staff).post("/api/v1/incentives/fuel/redeem/", {"code": award.code}, format="json")
    assert second.status_code == 409
    assert balance_of(get_system_account(incentive_services.FUEL_PAYABLE)) == Decimal("20.000")
    assert balance_of(get_system_account(incentive_services.FUEL_RESERVE)) == 0

    unknown = _client(staff).post("/api/v1/incentives/fuel/redeem/", {"code": "FUEL-NOPE"}, format="json")
    assert unknown.status_code == 409


@pytest.mark.django_db
def test_expired_fuel_code_cannot_be_redeemed(staff):
    campaign = _campaign(staff, IncentiveCampaign.Kind.DRIVER_FUEL)
    driver = User.objects.create_user(phone_number="+21655000009", password="x", role=User.Role.DRIVER)
    award = IncentiveAward.objects.create(
        campaign=campaign, user=driver, kind="DRIVER_FUEL", amount=Decimal("10.000"), code="FUEL-OLD",
        expires_at=timezone.now() - timezone.timedelta(days=1),
    )
    response = _client(staff).post("/api/v1/incentives/fuel/redeem/", {"code": award.code}, format="json")
    assert response.status_code == 409
    assert "expired" in response.data["detail"]


@pytest.mark.django_db
def test_campaign_api_is_staff_only_and_backfills_existing_verified_users(staff):
    early = User.objects.create_user(phone_number="+21655000010", password="x")
    _approve(early, VerificationDocument.DocType.CIN, staff)  # verified before any campaign
    passenger_client = _client(early)
    assert passenger_client.get("/api/v1/incentives/campaigns/").status_code == 403

    created = _client(staff).post(
        "/api/v1/incentives/campaigns/",
        {"name": "Launch", "kind": "WELCOME_CREDIT", "amount": "8.000", "is_active": True}, format="json",
    )
    assert created.status_code == 201
    campaign_id = created.data["id"]
    funded = _client(staff).post(
        f"/api/v1/incentives/campaigns/{campaign_id}/fund/", {"amount": "50.000", "idempotency_key": "k1"},
        format="json",
    )
    assert funded.data["balance"] == "50.000"
    replay = _client(staff).post(
        f"/api/v1/incentives/campaigns/{campaign_id}/fund/", {"amount": "50.000", "idempotency_key": "k1"},
        format="json",
    )
    assert replay.data["balance"] == "50.000"

    evaluated = _client(staff).post(f"/api/v1/incentives/campaigns/{campaign_id}/evaluate/")
    assert evaluated.data == {"awarded": 1}
    assert _client(staff).post(f"/api/v1/incentives/campaigns/{campaign_id}/evaluate/").data == {"awarded": 0}
    detail = _client(staff).get(f"/api/v1/incentives/campaigns/{campaign_id}/").data
    assert detail["balance"] == "42.000" and detail["awards_issued"] == 1

    bad = _client(staff).patch(f"/api/v1/incentives/campaigns/{campaign_id}/", {"kind": "DRIVER_FUEL"}, format="json")
    assert bad.status_code == 400


@pytest.mark.django_db
def test_open_voucher_codes_are_claimed_once_with_a_generic_error(staff):
    voucher = incentive_services.issue_voucher(amount=Decimal("5.000"), code="SUMMER-5")
    first = User.objects.create_user(phone_number="+21655000011", password="x")
    second = User.objects.create_user(phone_number="+21655000012", password="x")

    claimed = _client(first).post("/api/v1/incentives/vouchers/claim/", {"code": " summer-5 "}, format="json")
    assert claimed.status_code == 201 and claimed.data["code"] == "SUMMER-5"
    voucher.refresh_from_db()
    assert voucher.issued_to == first

    taken = _client(second).post("/api/v1/incentives/vouchers/claim/", {"code": "SUMMER-5"}, format="json")
    missing = _client(second).post("/api/v1/incentives/vouchers/claim/", {"code": "NOPE"}, format="json")
    assert taken.status_code == missing.status_code == 400
    assert taken.data["detail"] == missing.data["detail"] == "This code is not valid."
    assert Voucher.objects.filter(issued_to=second).count() == 0
