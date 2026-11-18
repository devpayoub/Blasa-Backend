from datetime import timedelta
from decimal import Decimal
from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bookings.models import Booking
from apps.interests import services as interest_services
from apps.matching.views import rank_key
from apps.notifications.models import Notification
from apps.rides import services as ride_services
from apps.rides.models import Ride
from apps.users.models import InterestTag, User
from apps.vehicles.models import Vehicle
from apps.verification.models import VerificationDocument

TUNIS = Point(10.18, 36.80, srid=4326)
SOUSSE = Point(10.64, 35.83, srid=4326)
GABES = Point(10.10, 33.88, srid=4326)
SEED = import_module("apps.users.migrations.0004_seed_interest_tags")


@pytest.fixture(autouse=True)
def tags():
    # Transactional tests elsewhere flush seeded rows, so reseed with the migration's own function.
    SEED.seed(django_apps, None)
    return {tag.slug: tag for tag in InterestTag.objects.all()}


def _user(phone, *, driver=False, interests=(), name="Synthetic Rider"):
    user = User.objects.create_user(
        phone_number=phone, password="x", role=User.Role.DRIVER if driver else User.Role.PASSENGER,
    )
    user.profile.full_name = name
    user.profile.save()
    user.profile.interests.set(InterestTag.objects.filter(slug__in=interests))
    return user


def _vehicle(driver):
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE,
        storage_key=f"verification/{driver.phone_number}.pdf", status=VerificationDocument.Status.APPROVED,
    )
    return Vehicle.objects.create(
        driver=driver, make="Synthetic", model="Sedan", plate_number=f"{driver.phone_number[-4:]} TU 9",
        seats=4, carte_grise=document,
    )


def _ride(driver, *, destination=SOUSSE, hours=24, **extra):
    return ride_services.publish_ride(
        driver=driver, vehicle=_vehicle(driver), origin=TUNIS, destination=destination,
        origin_label="Tunis", destination_label="Sousse", departure_at=timezone.now() + timedelta(hours=hours),
        seats_total=3, price_per_seat=Decimal("12.000"), **extra,
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _alert(passenger, hours=24):
    departure = timezone.now() + timedelta(hours=hours)
    return interest_services.create_interest(
        passenger=passenger, origin=TUNIS, destination=SOUSSE, origin_label="Tunis", destination_label="Sousse",
        earliest_departure=departure - timedelta(hours=4), latest_departure=departure + timedelta(hours=4), seats=1,
    )


@pytest.mark.django_db
def test_tag_catalogue_is_seeded_ordered_and_hides_inactive(tags):
    assert [t.slug for t in InterestTag.objects.all()] == [slug for slug, *_ in SEED.TAGS]
    InterestTag.objects.filter(slug="politics").update(is_active=False)
    response = _client(_user("+21651000001")).get("/api/v1/users/interest-tags/")
    assert response.status_code == 200
    assert "politics" not in [row["slug"] for row in response.data]
    assert response.data[0] == {"slug": "music", "labels": {"en": "Music", "fr": "Musique", "ar": "موسيقى"}}
    assert APIClient().get("/api/v1/users/interest-tags/").status_code == 401


@pytest.mark.django_db
def test_profile_interests_round_trip_and_reject_unknown_or_inactive(tags):
    client = _client(_user("+21651000002"))
    saved = client.patch("/api/v1/users/me/", {"profile": {"interests": ["tech", "music"]}}, format="json")
    assert saved.status_code == 200, saved.data
    assert sorted(client.get("/api/v1/users/me/").data["profile"]["interests"]) == ["music", "tech"]

    bad = client.patch("/api/v1/users/me/", {"profile": {"interests": ["tech", "astrology"]}}, format="json")
    assert bad.status_code == 400
    InterestTag.objects.filter(slug="sports").update(is_active=False)
    assert client.patch("/api/v1/users/me/", {"profile": {"interests": ["sports"]}}, format="json").status_code == 400
    # A name-only edit keeps the saved interests.
    client.patch("/api/v1/users/me/", {"profile": {"full_name": "Renamed"}}, format="json")
    assert sorted(client.get("/api/v1/users/me/").data["profile"]["interests"]) == ["music", "tech"]
    assert client.patch("/api/v1/users/me/", {"profile": {"interests": []}}, format="json").data["profile"][
        "interests"
    ] == []


@pytest.mark.django_db
def test_summaries_show_only_interests_shared_with_the_viewer():
    driver = _user("+21651000003", driver=True, interests=["music", "tech", "sports"])
    passenger = _user("+21651000004", interests=["tech", "music", "culture"])
    ride = _ride(driver)
    Booking.objects.create(
        ride=ride, passenger=passenger, seats=1, pickup_point=TUNIS, dropoff_point=SOUSSE,
        fare_amount=Decimal("12.000"),
    )

    summary = _client(passenger).get(f"/api/v1/rides/{ride.id}/").data["driver_summary"]
    assert sorted(summary["shared_interests"]) == ["music", "tech"]
    assert _client(driver).get(f"/api/v1/rides/{ride.id}/").data["driver_summary"]["shared_interests"] == []
    stranger = _user("+21651000005")
    assert _client(stranger).get(f"/api/v1/rides/{ride.id}/").data["driver_summary"]["shared_interests"] == []

    request = _client(driver).get("/api/v1/bookings/", {"ride": str(ride.id)}).data["results"][0]
    assert sorted(request["passenger_summary"]["shared_interests"]) == ["music", "tech"]
    mine = _client(passenger).get("/api/v1/bookings/").data["results"][0]
    assert mine["passenger_summary"]["shared_interests"] == []


@pytest.mark.django_db
def test_shared_interest_and_atmosphere_filters():
    music_driver = _user("+21651000006", driver=True, interests=["music"])
    tech_driver = _user("+21651000007", driver=True, interests=["tech"])
    plain_driver = _user("+21651000008", driver=True)
    musical = _ride(music_driver, atmosphere=Ride.Atmosphere.MUSIC)
    quiet = _ride(tech_driver, atmosphere=Ride.Atmosphere.QUIET)
    unset = _ride(plain_driver)
    viewer = _user("+21651000009", interests=["music"])

    def ids(user, **params):
        response = _client(user).get("/api/v1/rides/", params)
        assert response.status_code == 200, response.data
        return {row["id"] for row in response.data["results"]}

    assert ids(viewer, shared_interests="true") == {str(musical.id)}
    assert ids(viewer, shared_interests="false") >= {str(musical.id), str(quiet.id), str(unset.id)}
    assert ids(_user("+21651000010"), shared_interests="true") == set()
    assert ids(viewer, atmosphere="QUIET") == {str(quiet.id), str(unset.id)}
    assert ids(viewer, atmosphere="MUSIC", shared_interests="true") == {str(musical.id)}
    assert _client(viewer).get("/api/v1/rides/", {"atmosphere": "LOUD"}).status_code == 400


@pytest.mark.django_db
def test_ride_atmosphere_and_preferred_interests_publish_and_edit(tags):
    driver = _user("+21651000011", driver=True)
    client = _client(driver)
    created = client.post("/api/v1/rides/", {
        "vehicle": str(_vehicle(driver).id),
        "origin": {"type": "Point", "coordinates": [10.18, 36.80]},
        "destination": {"type": "Point", "coordinates": [10.64, 35.83]},
        "origin_label": "Tunis", "destination_label": "Sousse",
        "departure_at": (timezone.now() + timedelta(hours=30)).isoformat(),
        "seats_total": 2, "price_per_seat": "10.000",
        "atmosphere": "CONVERSATION", "preferred_interests": ["culture", "tech"],
    }, format="json")
    assert created.status_code == 201, created.data
    assert created.data["atmosphere"] == "CONVERSATION"
    assert sorted(created.data["preferred_interests"]) == ["culture", "tech"]

    url = f"/api/v1/rides/{created.data['id']}/"
    edited = client.patch(url, {"atmosphere": "", "preferred_interests": ["music"]}, format="json")
    assert edited.status_code == 200, edited.data
    assert edited.data["atmosphere"] == "" and edited.data["preferred_interests"] == ["music"]
    assert client.patch(url, {"preferred_interests": ["nope"]}, format="json").status_code == 400
    assert client.patch(url, {"atmosphere": "LOUD"}, format="json").status_code == 400


@pytest.mark.django_db
def test_matching_stays_route_first_and_interests_only_break_ties():
    passenger = _user("+21651000012", interests=["music", "tech", "sports"])
    alert = _alert(passenger)
    everything_shared = _user("+21651000013", driver=True, interests=["music", "tech", "sports"])
    wrong_route = _ride(everything_shared, destination=GABES)
    nothing_shared = _ride(_user("+21651000014", driver=True))
    shares_two = _ride(_user("+21651000015", driver=True, interests=["music", "tech"]))

    rows = _client(passenger).get(f"/api/v1/matching/interests/{alert.id}/rides/").data["results"]
    ids = [row["id"] for row in rows]
    assert str(wrong_route.id) not in ids
    assert ids == [str(shares_two.id), str(nothing_shared.id)]
    assert rows[0]["score"] == rows[1]["score"]

    later = timezone.now() + timedelta(hours=1)
    assert rank_key(90.4, 0, later, "b") < rank_key(89.9, 3, later, "a")
    assert rank_key(90.4, 2, later, "b") < rank_key(90.1, 0, later, "a")


@pytest.mark.django_db
def test_route_edit_rematches_once_and_non_route_edits_do_not(django_capture_on_commit_callbacks):
    driver = _user("+21651000016", driver=True)
    with django_capture_on_commit_callbacks(execute=True):
        ride = _ride(driver, destination=GABES)
    passenger = _user("+21651000017")
    alert = _alert(passenger)
    matches = Notification.objects.filter(user=passenger, kind="match.found")
    assert not matches.exists()

    owner = _client(driver)
    url = f"/api/v1/rides/{ride.id}/"
    with django_capture_on_commit_callbacks(execute=True):
        assert owner.patch(url, {"destination": {"type": "Point", "coordinates": [10.64, 35.83]}},
                           format="json").status_code == 200
    assert matches.count() == 1
    assert matches.get().payload == {"interest_id": str(alert.id), "ride_id": str(ride.id)}

    with django_capture_on_commit_callbacks(execute=True):
        assert owner.patch(url, {"seats_total": 2}, format="json").status_code == 200
    assert matches.count() == 1

    # With the alert cleared, a non-route edit must not rematch, but a route-term edit does.
    matches.delete()
    with django_capture_on_commit_callbacks(execute=True):
        assert owner.patch(url, {"notes": "Bring a jacket", "atmosphere": "QUIET"}, format="json").status_code == 200
    assert not matches.exists()
    with django_capture_on_commit_callbacks(execute=True):
        assert owner.patch(url, {"seats_total": 3}, format="json").status_code == 200
    assert matches.count() == 1
