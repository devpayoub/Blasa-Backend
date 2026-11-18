"""Phase 8: brand portal ownership, verified funding, promo provenance and analytics."""

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
from test_phase7_flow import _confirmed_booking, _make_booking, _make_driver, _pay, _staff_client, _topup, _transition

from apps.audit.models import AuditLog
from apps.brands.models import Brand, Campaign, CampaignEvent, SponsoredRide
from apps.brands.services import campaign_account
from apps.payments.gateways import GatewayError
from apps.payments.gateways.flouci import FlouciGateway
from apps.payments.models import PaymentIntent
from apps.rides import services as ride_services
from apps.users.models import InterestTag, User
from apps.vehicles.models import Vehicle
from apps.verification.models import VerificationDocument
from apps.wallet.services import balance_of

TUNIS = Point(10.18, 36.80, srid=4326)
SOUSSE = Point(10.64, 35.83, srid=4326)
FARE = Decimal("20.000")


def _brand_owner(phone: str, name: str) -> tuple[User, Brand]:
    owner = User.objects.create_user(phone_number=phone, password="x", role=User.Role.BRAND)
    return owner, Brand.objects.create(name=name, owner=owner)


def _campaign(brand: Brand, **fields) -> Campaign:
    defaults = {
        "brand": brand, "name": f"{brand.name} launch",
        "discount_per_seat": Decimal("5.000"), "budget": Decimal("50.000"),
        "promotion_title": "Powered by " + brand.name,
        "promotion_body": "A calmer commute, on us.",
    }
    return Campaign.objects.create(**{**defaults, **fields})


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def _ride(driver, vehicle):
    return ride_services.publish_ride(
        driver=driver, vehicle=vehicle, origin=TUNIS, destination=SOUSSE,
        origin_label="Tunis", destination_label="Sousse",
        departure_at=timezone.now() + timedelta(days=1), seats_total=3, price_per_seat=FARE,
    )


def _webhook(intent, event_id="evt_brand_1", amount=None):
    payload = {
        "event_id": event_id,
        "event": "payment.succeeded",
        "data": {"payment": {"id": intent.gateway_reference, "amount": str(amount or intent.amount)}},
    }
    body = json.dumps(payload).encode()
    signature = hmac.new(settings.FLUOCI_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return APIClient().post(
        "/api/v1/payments/webhooks/flouci/", body, content_type="application/json",
        HTTP_X_FLOUCI_SIGNATURE=signature,
    )


# ------------------------------------------------------------------- tenancy


@pytest.mark.django_db
def test_brand_cannot_reach_another_brands_campaigns_or_analytics():
    owner_a, brand_a = _brand_owner("+21680000001", "Aerialo")
    owner_b, brand_b = _brand_owner("+21680000002", "CactusAir")
    campaign_b = _campaign(brand_b)

    client_a = _client(owner_a)
    mine = client_a.get("/api/v1/brands/brands/mine/")
    assert mine.status_code == 200 and mine.data["id"] == str(brand_a.id)
    assert client_a.get("/api/v1/brands/brands/mine/").data["name"] == "Aerialo"

    listed = client_a.get("/api/v1/brands/campaigns/")
    assert listed.status_code == 200
    assert listed.data["results"] == []

    # Direct id access is scoped away, not merely permission-checked.
    assert client_a.get(f"/api/v1/brands/campaigns/{campaign_b.id}/").status_code == 404
    assert client_a.patch(
        f"/api/v1/brands/campaigns/{campaign_b.id}/", {"name": "hijacked"}, format="json",
    ).status_code == 404
    assert client_a.post(f"/api/v1/brands/campaigns/{campaign_b.id}/pause/").status_code == 404
    assert client_a.get(f"/api/v1/brands/campaigns/{campaign_b.id}/analytics/").status_code == 404
    assert client_a.get(f"/api/v1/brands/campaigns/{campaign_b.id}/promo-codes/").status_code == 404

    # Creating a campaign under another brand is refused even with a valid brand id.
    created = client_a.post(
        "/api/v1/brands/campaigns/",
        {"brand": str(brand_b.id), "name": "shadow", "discount_per_seat": "1.000", "budget": "10.000"},
        format="json",
    )
    assert created.status_code == 403
    # And a campaign can never be moved to another brand.
    own = _campaign(brand_a, name="own")
    moved = client_a.patch(
        f"/api/v1/brands/campaigns/{own.id}/", {"brand": str(brand_b.id)}, format="json",
    )
    assert moved.status_code == 400

    # Ordinary members cannot use the portal at all.
    passenger = User.objects.create_user(phone_number="+21680000003", password="x")
    assert _client(passenger).get("/api/v1/brands/campaigns/").status_code == 403
    assert _client(passenger).get("/api/v1/brands/brands/mine/").status_code == 403
    # Nor can they promote themselves through profile editing.
    promoted = _client(passenger).patch("/api/v1/users/me/", {"role": "BRAND"}, format="json")
    assert promoted.status_code == 200
    assert promoted.data["role"] == "PASSENGER"


@pytest.mark.django_db
def test_brand_creates_pauses_and_edits_its_own_campaign():
    owner, brand = _brand_owner("+21680000004", "Aerialo")
    client = _client(owner)

    created = client.post(
        "/api/v1/brands/campaigns/",
        {
            "brand": str(brand.id), "name": "Spring", "discount_per_seat": "4.000", "budget": "40.000",
            "audience_interests": ["music"], "promotion_title": "Spring rides",
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    campaign_id = created.data["id"]
    assert created.data["audience_interests"] == ["music"]
    assert created.data["balance"] == "0.000"

    paused = client.post(f"/api/v1/brands/campaigns/{campaign_id}/pause/")
    assert paused.status_code == 200 and paused.data["is_active"] is False
    assert paused.data["paused_at"] is not None
    resumed = client.post(f"/api/v1/brands/campaigns/{campaign_id}/resume/")
    assert resumed.data["is_active"] is True

    assert client.patch(
        f"/api/v1/brands/campaigns/{campaign_id}/", {"budget": "0.000"}, format="json",
    ).status_code == 400
    assert client.patch(
        f"/api/v1/brands/campaigns/{campaign_id}/",
        {"starts_at": "2026-12-02T10:00:00Z", "ends_at": "2026-12-01T10:00:00Z"}, format="json",
    ).status_code == 400


# ------------------------------------------------------------------- funding


@pytest.mark.django_db
def test_brand_funding_requires_a_verified_payment_and_never_uses_cash():
    owner, brand = _brand_owner("+21680000005", "Aerialo")
    campaign = _campaign(brand)
    client = _client(owner)

    with patch.object(FlouciGateway, "create_intent", autospec=True) as mock_create:
        def fake_create(self, intent):
            intent.gateway_reference = f"pay_{intent.pk}"
            intent.save(update_fields=["gateway_reference", "updated_at"])
            return {"payment_url": "https://pay.flouci.com/pay/x", "gateway_reference": intent.gateway_reference}

        mock_create.side_effect = fake_create
        started = client.post(
            f"/api/v1/brands/campaigns/{campaign.id}/funding-intents/", {"amount": "30.000"}, format="json",
        )
    assert started.status_code == 201, started.data
    intent = PaymentIntent.objects.get(pk=started.data["intent"])
    assert intent.kind == PaymentIntent.Kind.CAMPAIGN_FUNDING
    assert intent.gateway == PaymentIntent.Gateway.FLOUCI
    assert intent.campaign_id == campaign.id

    # Until the provider settles, no credit exists anywhere.
    assert balance_of(campaign_account(campaign)) == 0
    assert campaign.spent == 0

    assert _webhook(intent, event_id="evt_brand_credit").status_code == 200
    campaign.refresh_from_db()
    assert balance_of(campaign_account(campaign)) == Decimal("30.000")
    assert PaymentIntent.objects.get(pk=intent.pk).status == "PAID"
    assert AuditLog.objects.filter(action="brand.campaign_funded", object_id=str(campaign.id)).exists()

    # A replayed delivery credits once.
    assert _webhook(intent, event_id="evt_brand_credit").status_code == 200
    assert balance_of(campaign_account(campaign)) == Decimal("30.000")

    # A brand can never create credit through the staff reconciliation path.
    assert client.post(
        f"/api/v1/brands/campaigns/{campaign.id}/fund/", {"amount": "100.000"}, format="json",
    ).status_code == 403


@pytest.mark.django_db
def test_funding_intent_failure_leaves_no_spendable_credit():
    owner, brand = _brand_owner("+21680000006", "Aerialo")
    campaign = _campaign(brand)

    with patch.object(FlouciGateway, "create_intent", autospec=True, side_effect=GatewayError("down")):
        response = _client(owner).post(
            f"/api/v1/brands/campaigns/{campaign.id}/funding-intents/", {"amount": "10.000"}, format="json",
        )
    assert response.status_code == 400
    intent = PaymentIntent.objects.get(campaign=campaign)
    assert intent.status == "FAILED"
    assert balance_of(campaign_account(campaign)) == 0


# ------------------------------------------------------- sponsored content


@pytest.mark.django_db
def test_sponsored_ride_is_labeled_and_discounts_only_the_declared_audience():
    driver, vehicle = _make_driver("+21680000007")
    owner, brand = _brand_owner("+21680000008", "Aerialo")
    campaign = _campaign(brand)  # no audience tags: every member is eligible
    ride = _ride(driver, vehicle)
    SponsoredRide.objects.create(campaign=campaign, ride=ride)

    summary = _client(owner).get(f"/api/v1/brands/sponsorship/{ride.id}/")
    assert summary.status_code == 200
    assert summary.data["label"] == "Sponsored"
    assert summary.data["brand_name"] == "Aerialo"
    assert summary.data["active"] is True
    assert summary.data["title"] == "Powered by Aerialo"
    # Aggregate only: no member identifiers or interests.
    assert set(summary.data) == {
        "campaign", "brand_name", "discount_per_seat", "label", "title", "body", "url", "active",
    }

    passenger = User.objects.create_user(phone_number="+21680000009", password="x")
    booking = _make_booking(driver, vehicle, passenger, ride=ride)
    _topup(passenger, FARE)
    driver_client, passenger_client = _client(driver), _client(passenger)
    assert _transition(driver_client, booking, "accept").status_code == 200
    quote = passenger_client.get(f"/api/v1/bookings/{booking.id}/payment-quote/").data
    assert quote["subsidy"] == "5.000"
    assert quote["out_of_pocket"] == "15.000"

    # A targeted campaign excludes a member whose declared interests do not match.
    targeted = _campaign(brand, name="Music only", discount_per_seat=Decimal("6.000"))
    targeted.audience_interests.set([InterestTag.objects.get(slug="music")])
    SponsoredRide.objects.filter(campaign=campaign).delete()
    SponsoredRide.objects.create(campaign=targeted, ride=ride)

    no_music_quote = passenger_client.get(f"/api/v1/bookings/{booking.id}/payment-quote/").data
    assert no_music_quote["subsidy"] == "0.000"
    passenger_client.patch(
        "/api/v1/users/me/", {"profile": {"full_name": "Lina B.", "interests": ["music"]}}, format="json",
    )
    music_quote = passenger_client.get(f"/api/v1/bookings/{booking.id}/payment-quote/").data
    assert music_quote["subsidy"] == "6.000"


@pytest.mark.django_db
def test_brand_cannot_sponsor_its_own_ride():
    owner, brand = _brand_owner("+21680000010", "Aerialo")
    campaign = _campaign(brand)
    document = VerificationDocument.objects.create(
        user=owner, doc_type=VerificationDocument.DocType.CARTE_GRISE, storage_key="verification/x.pdf",
        status=VerificationDocument.Status.APPROVED,
    )
    vehicle = Vehicle.objects.create(
        driver=owner, make="Dacia", model="Logan", plate_number="9999 TU 1234", seats=4, carte_grise=document,
    )
    owner.role = User.Role.DRIVER
    owner.save(update_fields=["role"])
    ride = _ride(owner, vehicle)
    owner.role = User.Role.BRAND
    owner.save(update_fields=["role"])
    response = _client(owner).post(
        f"/api/v1/brands/campaigns/{campaign.id}/sponsored-rides/", {"ride": str(ride.id)}, format="json",
    )
    assert response.status_code == 400
    assert "own rides" in response.data["detail"]


# ------------------------------------------------------------- promo codes


@pytest.mark.django_db
def test_promo_codes_are_budget_bounded_and_report_their_redemption():
    driver, vehicle = _make_driver("+21680000011")
    owner, brand = _brand_owner("+21680000012", "Aerialo")
    campaign = _campaign(brand, budget=Decimal("20.000"), discount_per_seat=Decimal("5.000"))
    client = _client(owner)

    issued = client.post(
        f"/api/v1/brands/campaigns/{campaign.id}/promo-codes/", {"amount": "5.000"}, format="json",
    )
    assert issued.status_code == 201, issued.data
    code = issued.data["code"]
    assert code.startswith("BRAND")

    over = client.post(
        f"/api/v1/brands/campaigns/{campaign.id}/promo-codes/", {"amount": "50.000"}, format="json",
    )
    assert over.status_code == 400

    listed = client.get(f"/api/v1/brands/campaigns/{campaign.id}/promo-codes/")
    assert listed.status_code == 200 and len(listed.data) == 1

    # A passenger redeems the brand code on a real booking.
    passenger = User.objects.create_user(phone_number="+21680000013", password="x")
    ride = _ride(driver, vehicle)
    booking = _make_booking(driver, vehicle, passenger, ride=ride)
    _topup(passenger, FARE)
    driver_client, passenger_client = _client(driver), _client(passenger)
    assert passenger_client.post("/api/v1/incentives/vouchers/claim/", {"code": code}, format="json").status_code == 201
    assert _transition(driver_client, booking, "accept").status_code == 200
    assert _pay(passenger_client, booking).status_code == 200

    redemption = CampaignEvent.objects.get(campaign=campaign, dedupe_key=f"promo:{booking.id}")
    assert redemption.kind == CampaignEvent.Kind.REDEMPTION
    assert redemption.amount == Decimal("5.000")

    analytics = client.get(f"/api/v1/brands/campaigns/{campaign.id}/analytics/").data
    assert analytics["promo_codes_issued"] == 1
    assert analytics["promo_redemption_value"] == "5.000"
    assert analytics["promo_value_outstanding"] == "0.000"
    # Analytics never name a member or expose their interests.
    assert "passenger" not in str(analytics)


@pytest.mark.django_db
def test_impressions_and_engagement_are_deduplicated_and_redemptions_are_not_client_writable():
    driver, vehicle = _make_driver("+21680000014")
    owner, brand = _brand_owner("+21680000015", "Aerialo")
    campaign = _campaign(brand)
    ride = _ride(driver, vehicle)
    SponsoredRide.objects.create(campaign=campaign, ride=ride)
    passenger = User.objects.create_user(phone_number="+21680000016", password="x")
    client = _client(passenger)

    # The member who saw the card reports it; the server derives the dedupe key.
    first = client.post(f"/api/v1/brands/sponsorship/{ride.id}/events/", {"kind": "IMPRESSION"}, format="json")
    assert first.status_code == 202 and first.data["recorded"] is True
    replay = client.post(f"/api/v1/brands/sponsorship/{ride.id}/events/", {"kind": "IMPRESSION"}, format="json")
    assert replay.status_code == 202 and replay.data["recorded"] is False
    assert client.post(
        f"/api/v1/brands/sponsorship/{ride.id}/events/", {"kind": "ENGAGEMENT"}, format="json",
    ).data["recorded"] is True

    # Financial outcomes are server-derived only.
    assert client.post(
        f"/api/v1/brands/sponsorship/{ride.id}/events/", {"kind": "REDEMPTION"}, format="json",
    ).status_code == 400
    assert client.post(
        f"/api/v1/brands/sponsorship/{ride.id}/events/", {"kind": "GRANT"}, format="json",
    ).status_code == 400

    analytics = _client(owner).get(f"/api/v1/brands/campaigns/{campaign.id}/analytics/").data
    assert analytics["impressions"] == 1
    assert analytics["engagements"] == 1
    assert analytics["redemptions"] == 0


@pytest.mark.django_db
def test_subsidy_reversal_removes_the_redemption_from_analytics():
    driver, vehicle = _make_driver("+21680000015")
    owner, brand = _brand_owner("+21680000016", "Aerialo")
    campaign = _campaign(brand)
    campaign.audience_interests.set([])
    ride = _ride(driver, vehicle)
    SponsoredRide.objects.create(campaign=campaign, ride=ride)
    passenger = User.objects.create_user(phone_number="+21680000017", password="x")
    _confirmed_booking(driver, vehicle, passenger)  # its own ride, no sponsorship

    # The sponsor account must be funded before it can pay a discount.
    staff_client, _ = _staff_client()
    assert staff_client.post(
        f"/api/v1/brands/campaigns/{campaign.id}/fund/", {"amount": "50.000"}, format="json",
    ).status_code == 201

    # Sponsor a ride, confirm a booking on it, then cancel it: spend and analytics both reset.
    sponsored_booking = _make_booking(driver, vehicle, passenger, ride=ride)
    _topup(passenger, FARE)
    assert _transition(_client(driver), sponsored_booking, "accept").status_code == 200
    assert _pay(_client(passenger), sponsored_booking).status_code == 200

    campaign.refresh_from_db()
    assert campaign.spent == Decimal("5.000")
    analytics = _client(owner).get(f"/api/v1/brands/campaigns/{campaign.id}/analytics/").data
    assert analytics["redemptions"] == 1
    assert analytics["subsidy_spend"] == "5.000"

    assert _client(passenger).post(
        f"/api/v1/bookings/{sponsored_booking.id}/transitions/cancel_passenger/"
    ).status_code == 200
    campaign.refresh_from_db()
    assert campaign.spent == Decimal("0.000")
    after = _client(owner).get(f"/api/v1/brands/campaigns/{campaign.id}/analytics/").data
    assert after["redemptions"] == 0
    assert after["subsidy_spend"] == "0.000"
