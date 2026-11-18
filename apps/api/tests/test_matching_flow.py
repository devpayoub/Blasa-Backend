from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from apps.interests import services as interest_services
from apps.matching import scoring
from apps.matching.selectors import matching_interests_for_ride, matching_rides_for_interest
from apps.matching.tasks import match_interest_to_rides, match_ride_to_interests
from apps.rides import services as ride_services
from apps.users.models import User
from apps.vehicles.models import Vehicle
from apps.verification.models import VerificationDocument

TUNIS = Point(10.18, 36.80, srid=4326)
LAC_PICKUP = Point(10.24, 36.83, srid=4326)      # ~7 km from Tunis centre
SOUSSE = Point(10.64, 35.83, srid=4326)
GABES = Point(10.10, 33.88, srid=4326)           # ~330 km south — never matches


def _make_driver(phone: str):
    driver = User.objects.create_user(phone_number=phone, password="x", role=User.Role.DRIVER)
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE, storage_key=f"verification/{phone}.pdf",
        status=VerificationDocument.Status.APPROVED,
    )
    vehicle = Vehicle.objects.create(
        driver=driver, make="Dacia", model="Logan", plate_number=f"{phone[-4:]} TU 3333",
        seats=4, carte_grise=document,
    )
    return driver, vehicle


def _publish_ride(driver, vehicle, origin=TUNIS, destination=SOUSSE, seats_total=2,
                  price=Decimal("15.000"), departure_at=None):
    return ride_services.publish_ride(
        driver=driver, vehicle=vehicle,
        origin=origin, destination=destination,
        origin_label="Origin", destination_label="Destination",
        departure_at=departure_at or timezone.now() + timedelta(hours=24),
        seats_total=seats_total, price_per_seat=price,
    )


def _make_interest(passenger, origin=TUNIS, destination=SOUSSE,
                   earliest=None, latest=None, seats=1):
    earliest = earliest or timezone.now() + timedelta(hours=12)
    latest = latest or earliest + timedelta(hours=24)
    return interest_services.create_interest(
        passenger=passenger, origin=origin, destination=destination,
        origin_label="Tunis", destination_label="Sousse",
        earliest_departure=earliest, latest_departure=latest, seats=seats,
    )


# ---------------------------------------------------------------- scoring


@pytest.mark.django_db
def test_scorer_perfect_alignment_scores_max():
    driver, vehicle = _make_driver("+21650000001")
    departure = timezone.now() + timedelta(hours=24)
    ride = _publish_ride(driver, vehicle, departure_at=departure)
    # Same endpoints, window centered exactly on the departure.
    interest = _make_interest(
        User.objects.create_user(phone_number="+21650000002", password="x"),
        origin=TUNIS, destination=SOUSSE,
        earliest=departure - timedelta(hours=4), latest=departure + timedelta(hours=4),
    )
    assert scoring.score_interest_ride(interest=interest, ride=ride) == 100.0


@pytest.mark.django_db
def test_scorer_sanity_imperfect_match_ranks_lower():
    driver, vehicle = _make_driver("+21650000003")
    departure = timezone.now() + timedelta(hours=24)
    ride = _publish_ride(driver, vehicle, departure_at=departure)

    aligned = _make_interest(
        User.objects.create_user(phone_number="+21650000004", password="x"),
        origin=TUNIS, destination=SOUSSE,
        earliest=departure - timedelta(hours=4), latest=departure + timedelta(hours=4),
    )
    # Pickup 7 km away and window edge — must still match, but rank below aligned.
    off = _make_interest(
        User.objects.create_user(phone_number="+21650000005", password="x"),
        origin=LAC_PICKUP, destination=SOUSSE,
        earliest=departure - timedelta(hours=16), latest=departure + timedelta(hours=16),
    )
    aligned_score = scoring.score_interest_ride(interest=aligned, ride=ride)
    off_score = scoring.score_interest_ride(interest=off, ride=ride)
    assert aligned_score == 100.0
    assert 0 < off_score < aligned_score


@pytest.mark.django_db
def test_scorer_rejects_impossible_pairs():
    driver, vehicle = _make_driver("+21650000006")
    departure = timezone.now() + timedelta(hours=24)
    ride = _publish_ride(driver, vehicle, seats_total=1, departure_at=departure)
    passenger = User.objects.create_user(phone_number="+21650000007", password="x")

    # Outside the departure window entirely.
    outside = _make_interest(
        passenger, origin=TUNIS, destination=SOUSSE,
        earliest=departure + timedelta(days=5), latest=departure + timedelta(days=6),
    )
    assert scoring.score_interest_ride(interest=outside, ride=ride) is None

    # Not enough seats left for what the passenger wants.
    full = _make_interest(passenger, seats=2, earliest=departure - timedelta(hours=4),
                          latest=departure + timedelta(hours=4))
    assert scoring.score_interest_ride(interest=full, ride=ride) is None


# ---------------------------------------------------------------- matching pipeline


@pytest.mark.django_db
def test_geo_precision_near_matches_far_does_not():
    driver, vehicle = _make_driver("+21650000008")
    passenger = User.objects.create_user(phone_number="+21650000009", password="x")

    near = _make_interest(passenger, origin=TUNIS, destination=SOUSSE)
    far = _make_interest(passenger, origin=GABES, destination=SOUSSE)

    ride = _publish_ride(driver, vehicle, origin=TUNIS, destination=SOUSSE)

    ride_matches = list(matching_interests_for_ride(ride))
    assert near in ride_matches
    assert far not in ride_matches

    far_matches = list(matching_rides_for_interest(far))
    assert far_matches == []


@pytest.mark.django_db
def test_ride_publish_fires_matching_task_in_eager_mode():
    driver, vehicle = _make_driver("+21650000010")
    passenger = User.objects.create_user(phone_number="+21650000011", password="x")
    _make_interest(passenger, origin=TUNIS, destination=SOUSSE)

    # CELERY_TASK_ALWAYS_EAGER=True: publishing must run the matcher inline and
    # surface the interest through the driver's matches feed.
    ride = _publish_ride(driver, vehicle)
    assert match_ride_to_interests.delay(ride_id=str(ride.id)).result >= 1

    client = APIClient()
    client.force_authenticate(driver)
    response = client.get(f"/api/v1/matching/rides/{ride.id}/interests/")
    assert response.status_code == 200
    results = response.data["results"]
    assert len(results) == 1
    assert results[0]["score"] > 0


@pytest.mark.django_db
def test_interest_registration_fires_matching_task_in_eager_mode():
    driver, vehicle = _make_driver("+21650000012")
    ride = _publish_ride(driver, vehicle)
    passenger = User.objects.create_user(phone_number="+21650000013", password="x")
    client = APIClient()
    client.force_authenticate(passenger)

    response = client.post(
        "/api/v1/interests/",
        {
            "origin": {"type": "Point", "coordinates": [10.18, 36.80]},
            "destination": {"type": "Point", "coordinates": [10.64, 35.83]},
            "origin_label": "Tunis", "destination_label": "Sousse",
            "earliest_departure": (timezone.now() + timedelta(hours=12)).isoformat(),
            "latest_departure": (timezone.now() + timedelta(hours=36)).isoformat(),
            "seats": 1,
        },
        format="json",
    )
    assert response.status_code == 201
    interest_id = response.data["id"]
    assert match_interest_to_rides.delay(interest_id=interest_id).result >= 1

    response = client.get(f"/api/v1/matching/interests/{interest_id}/rides/")
    assert response.status_code == 200
    results = response.data["results"]
    assert [row["id"] for row in results] == [str(ride.id)]
    assert results[0]["score"] > 0


@pytest.mark.django_db
def test_match_feeds_are_scoped_to_their_owner():
    driver, vehicle = _make_driver("+21650000014")
    ride = _publish_ride(driver, vehicle)
    passenger = User.objects.create_user(phone_number="+21650000015", password="x")
    interest = _make_interest(passenger)

    stranger = APIClient()
    stranger.force_authenticate(User.objects.create_user(phone_number="+21650000016", password="x"))
    assert stranger.get(f"/api/v1/matching/rides/{ride.id}/interests/").status_code == 404
    assert stranger.get(f"/api/v1/matching/interests/{interest.id}/rides/").status_code == 404

    # The owner sees them.
    owner = APIClient()
    owner.force_authenticate(passenger)
    assert owner.get(f"/api/v1/matching/interests/{interest.id}/rides/").status_code == 200
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    assert driver_client.get(f"/api/v1/matching/rides/{ride.id}/interests/").status_code == 200


@pytest.mark.django_db
def test_interest_lifecycle_validation_and_cancellation():
    passenger = User.objects.create_user(phone_number="+21650000017", password="x")
    client = APIClient()
    client.force_authenticate(passenger)

    # Window ends before it starts.
    bad = client.post(
        "/api/v1/interests/",
        {
            "origin": {"type": "Point", "coordinates": [10.18, 36.80]},
            "destination": {"type": "Point", "coordinates": [10.64, 35.83]},
            "earliest_departure": (timezone.now() + timedelta(days=2)).isoformat(),
            "latest_departure": (timezone.now() + timedelta(days=1)).isoformat(),
        },
        format="json",
    )
    assert bad.status_code == 400

    interest = _make_interest(passenger)
    cancelled = client.delete(f"/api/v1/interests/{interest.id}/")
    assert cancelled.status_code == 204
    interest.refresh_from_db()
    assert interest.is_active is False
    listed = client.get("/api/v1/interests/")
    assert listed.status_code == 200
    assert listed.data["results"] == []

    # The scorer itself is content-only; the active flag is filtered by the selectors.
    driver, vehicle = _make_driver("+21650000018")
    ride = _publish_ride(driver, vehicle, departure_at=timezone.now() + timedelta(hours=24))
    assert scoring.score_interest_ride(interest=interest, ride=ride) is not None
    assert list(matching_interests_for_ride(ride)) == []
