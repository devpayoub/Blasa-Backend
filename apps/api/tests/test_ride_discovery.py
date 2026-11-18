from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bookings.models import Booking
from apps.rides import services as ride_services
from apps.rides.models import Ride
from apps.users.models import User
from apps.vehicles.models import Vehicle
from apps.verification.models import VerificationDocument

TUNIS = Point(10.1815, 36.8065, srid=4326)
LA_MARSA = Point(10.3247, 36.8782, srid=4326)  # ~14 km from Tunis centre
SOUSSE = Point(10.6084, 35.8256, srid=4326)
SFAX = Point(10.7603, 34.7406, srid=4326)
SEARCH = {"from_lat": 36.8065, "from_lng": 10.1815, "to_lat": 35.8256, "to_lng": 10.6084}


def _driver(phone, name="Amira Ben Salah"):
    driver = User.objects.create_user(phone_number=phone, password="x", role=User.Role.DRIVER)
    driver.profile.full_name = name
    driver.profile.save()
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE,
        storage_key=f"verification/{phone}.pdf", status=VerificationDocument.Status.APPROVED,
    )
    vehicle = Vehicle.objects.create(
        driver=driver, make="Synthetic", model="Sedan", color="Blue", plate_number=f"{phone[-4:]} TU 7", seats=4,
        carte_grise=document,
    )
    return driver, vehicle


def _ride(driver, vehicle, *, origin=TUNIS, destination=SOUSSE, hours=24, price="15.000", seats=3):
    return ride_services.publish_ride(
        driver=driver, vehicle=vehicle, origin=origin, destination=destination,
        origin_label="Synthetic origin", destination_label="Synthetic destination",
        departure_at=timezone.now() + timedelta(hours=hours), seats_total=seats, price_per_seat=Decimal(price),
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _ids(response):
    assert response.status_code == 200, response.data
    return [row["id"] for row in response.data["results"]]


@pytest.mark.django_db
def test_search_requires_both_endpoints_to_match():
    driver, vehicle = _driver("+21677000001")
    match = _ride(driver, vehicle)
    wrong_destination = _ride(driver, vehicle, destination=SFAX)
    wrong_origin = _ride(driver, vehicle, origin=SFAX)
    near_pickup = _ride(driver, vehicle, origin=LA_MARSA)
    searcher = User.objects.create_user(phone_number="+21677000002", password="x")

    ids = _ids(_client(searcher).get("/api/v1/rides/", SEARCH))
    assert set(ids) == {str(match.id)}
    assert str(wrong_destination.id) not in ids and str(wrong_origin.id) not in ids

    wider = _ids(_client(searcher).get("/api/v1/rides/", {**SEARCH, "from_radius_km": 20}))
    assert set(wider) == {str(match.id), str(near_pickup.id)}
    rows = _client(searcher).get("/api/v1/rides/", {**SEARCH, "from_radius_km": 20, "ordering": "pickup"}).data
    assert [r["id"] for r in rows["results"]] == [str(match.id), str(near_pickup.id)]
    assert rows["results"][0]["distance_km"] == 0 and rows["results"][1]["distance_km"] > 10


@pytest.mark.django_db
def test_search_time_price_seat_boundaries_and_own_rides():
    driver, vehicle = _driver("+21677000003")
    early = _ride(driver, vehicle, hours=5, price="10.000", seats=1)
    late = _ride(driver, vehicle, hours=30, price="20.000", seats=3)
    full = _ride(driver, vehicle, hours=10)
    Ride.objects.filter(pk=full.pk).update(status=Ride.Status.FULL, seats_available=0)
    searcher = User.objects.create_user(phone_number="+21677000004", password="x")
    client = _client(searcher)

    assert _ids(client.get("/api/v1/rides/", SEARCH)) == [str(early.id), str(late.id)]
    after = early.departure_at.isoformat()
    assert _ids(client.get("/api/v1/rides/", {**SEARCH, "departure_after": after})) == [str(early.id), str(late.id)]
    before = (early.departure_at + timedelta(minutes=1)).isoformat()
    assert _ids(client.get("/api/v1/rides/", {**SEARCH, "departure_before": before})) == [str(early.id)]
    assert _ids(client.get("/api/v1/rides/", {**SEARCH, "max_price": "10.000"})) == [str(early.id)]
    assert _ids(client.get("/api/v1/rides/", {**SEARCH, "min_seats": 2})) == [str(late.id)]
    assert _ids(client.get("/api/v1/rides/", {**SEARCH, "min_seats": 4})) == []
    cheapest = _ids(client.get("/api/v1/rides/", {"ordering": "price"}))
    assert cheapest == [str(early.id), str(late.id)]
    assert _ids(_client(driver).get("/api/v1/rides/", SEARCH)) == []


@pytest.mark.django_db
@pytest.mark.parametrize("params", [
    {"from_lat": 36.8},
    {"from_lat": 95, "from_lng": 10.1},
    {"to_lat": 35.8, "to_lng": 200},
    {"from_radius_km": 5},
    {**SEARCH, "to_radius_km": 0.1},
    {**SEARCH, "from_radius_km": 500},
    {"ordering": "pickup"},
    {"ordering": "rating"},
    {"min_seats": 0},
    {"max_price": "-1"},
    {"departure_after": "2026-11-20T10:00:00Z", "departure_before": "2026-11-20T09:00:00Z"},
    {"departure_after": "not-a-date"},
])
def test_search_rejects_invalid_parameters(params):
    searcher = User.objects.create_user(phone_number="+21677000005", password="x")
    response = _client(searcher).get("/api/v1/rides/", params)
    assert response.status_code == 400, response.data


@pytest.mark.django_db
def test_search_paginates_and_returns_empty_pages():
    driver, vehicle = _driver("+21677000006")
    rides = [_ride(driver, vehicle, hours=5 + i) for i in range(3)]
    client = _client(User.objects.create_user(phone_number="+21677000007", password="x"))

    first = client.get("/api/v1/rides/", {**SEARCH, "page_size": 2})
    assert first.data["count"] == 3 and first.data["next"] and _ids(first) == [str(r.id) for r in rides[:2]]
    second = client.get("/api/v1/rides/", {**SEARCH, "page_size": 2, "page": 2})
    assert _ids(second) == [str(rides[2].id)] and second.data["next"] is None
    empty = client.get("/api/v1/rides/", {**SEARCH, "max_price": "1.000"})
    assert empty.data["count"] == 0 and empty.data["results"] == []


@pytest.mark.django_db
def test_driver_schedule_buckets_are_owner_scoped():
    driver, vehicle = _driver("+21677000008")
    upcoming_late = _ride(driver, vehicle, hours=40)
    upcoming_soon = _ride(driver, vehicle, hours=4)
    active, completed, cancelled = (_ride(driver, vehicle, hours=8 + i) for i in range(3))
    Ride.objects.filter(pk=active.pk).update(status=Ride.Status.IN_PROGRESS)
    Ride.objects.filter(pk=completed.pk).update(status=Ride.Status.COMPLETED)
    Ride.objects.filter(pk=cancelled.pk).update(status=Ride.Status.CANCELLED)
    other, other_vehicle = _driver("+21677000009", name="Other Driver")
    _ride(other, other_vehicle)
    client = _client(driver)

    def bucket(when):
        return _ids(client.get("/api/v1/rides/", {"scope": "mine", "when": when}))

    assert bucket("upcoming") == [str(upcoming_soon.id), str(upcoming_late.id)]
    assert bucket("active") == [str(active.id)]
    assert bucket("completed") == [str(completed.id)]
    assert bucket("cancelled") == [str(cancelled.id)]
    assert len(_ids(client.get("/api/v1/rides/", {"scope": "mine"}))) == 5
    assert client.get("/api/v1/rides/", {"scope": "mine", "when": "someday"}).status_code == 400


@pytest.mark.django_db
def test_participant_summaries_expose_no_contact_details():
    driver, vehicle = _driver("+21677000010")
    driver.email = "driver-synthetic@example.test"
    driver.save()
    done = _ride(driver, vehicle, hours=3)
    Ride.objects.filter(pk=done.pk).update(status=Ride.Status.COMPLETED)
    ride = _ride(driver, vehicle)
    passenger = User.objects.create_user(phone_number="+21677000011", password="x")
    passenger.profile.full_name = "Youssef"
    passenger.profile.save()
    booking = Booking.objects.create(
        ride=ride, passenger=passenger, seats=1, pickup_point=TUNIS, dropoff_point=SOUSSE,
        fare_amount=Decimal("15.000"),
    )
    other_ride = _ride(driver, vehicle, hours=50)
    Booking.objects.create(
        ride=other_ride, passenger=passenger, seats=1, pickup_point=TUNIS, dropoff_point=SOUSSE,
        fare_amount=Decimal("15.000"),
    )

    row = _client(passenger).get(f"/api/v1/rides/{ride.id}/").data
    assert row["driver_summary"] == {
        "id": str(driver.id), "display_name": "Amira S.", "average_rating": "0.00", "completed_trips": 1,
        "shared_interests": [],
    }
    assert row["vehicle_summary"] == {"make": "Synthetic", "model": "Sedan", "color": "Blue", "seats": 4}
    listed = _client(passenger).get("/api/v1/rides/", {"scope": "joined"}).data["results"]
    assert {r["driver_summary"]["completed_trips"] for r in listed} == {1}

    requests = _client(driver).get("/api/v1/bookings/", {"ride": str(ride.id)}).data["results"]
    assert [r["id"] for r in requests] == [str(booking.id)]
    summary = requests[0]["passenger_summary"]
    assert summary["display_name"] == "Youssef" and summary["completed_trips"] == 0
    flat = str(requests[0]) + str(row)
    for secret in ("+21677000010", "+21677000011", "driver-synthetic@example.test", "TU 7"):
        assert secret not in flat
    stranger = User.objects.create_user(phone_number="+21677000012", password="x")
    assert _client(stranger).get("/api/v1/bookings/", {"ride": str(ride.id)}).data["results"] == []
