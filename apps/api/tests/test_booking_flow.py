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

PICKUP = Point(10.18, 36.80, srid=4326)   # Tunis
DROPOFF = Point(10.64, 35.83, srid=4326)  # Sousse


def _make_driver(phone: str):
    driver = User.objects.create_user(phone_number=phone, password="x", role=User.Role.DRIVER)
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE, storage_key=f"verification/{phone}.pdf",
        status=VerificationDocument.Status.APPROVED,
    )
    vehicle = Vehicle.objects.create(
        driver=driver, make="Dacia", model="Logan", plate_number=f"{phone[-4:]} TU 1111",
        seats=4, carte_grise=document,
    )
    return driver, vehicle


def _make_ride(driver, vehicle, seats_total=2, price=Decimal("15.000")):
    return ride_services.publish_ride(
        driver=driver, vehicle=vehicle,
        origin=PICKUP, destination=DROPOFF,
        origin_label="Tunis", destination_label="Sousse",
        departure_at=timezone.now() + timedelta(days=1),
        seats_total=seats_total, price_per_seat=price,
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


@pytest.mark.django_db
def test_booking_fare_is_computed_server_side():
    driver, vehicle = _make_driver("+21630000001")
    ride = _make_ride(driver, vehicle, seats_total=3, price=Decimal("15.000"))
    passenger = User.objects.create_user(phone_number="+21630000002", password="x")
    client = APIClient()
    client.force_authenticate(passenger)

    response = _create_booking(client, ride, seats=2)
    assert response.status_code == 201
    assert response.data["fare_amount"] == "30.000"
    booking = Booking.objects.get(pk=response.data["id"])
    assert booking.fare_amount == Decimal("30.000")
    assert booking.status == "REQUESTED"
    # Creating a booking does not consume seats yet — seats are held at acceptance.
    ride.refresh_from_db()
    assert ride.seats_available == 3


@pytest.mark.django_db
def test_passenger_cannot_hold_two_active_bookings_on_one_ride():
    driver, vehicle = _make_driver("+21630000091")
    ride = _make_ride(driver, vehicle, seats_total=3)
    passenger = User.objects.create_user(phone_number="+21630000092", password="x")
    client = APIClient()
    client.force_authenticate(passenger)

    first = Booking.objects.get(pk=_create_booking(client, ride).data["id"])
    duplicate = _create_booking(client, ride)
    assert duplicate.status_code == 400
    assert "already have a booking" in str(duplicate.data)

    assert _transition(client, first, "cancel_passenger").status_code == 200
    assert _create_booking(client, ride).status_code == 201


@pytest.mark.django_db
def test_accepting_a_booking_reserves_seats():
    driver, vehicle = _make_driver("+21630000003")
    ride = _make_ride(driver, vehicle, seats_total=3)
    passenger = User.objects.create_user(phone_number="+21630000004", password="x")
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)

    booking = Booking.objects.get(pk=_create_booking(passenger_client, ride, seats=2).data["id"])
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    response = _transition(driver_client, booking, "accept")
    assert response.status_code == 200
    assert response.data["status"] == "ACCEPTED"

    ride.refresh_from_db()
    assert ride.seats_available == 1


@pytest.mark.django_db
def test_only_the_right_actor_can_transition():
    driver, vehicle = _make_driver("+21630000040")
    ride = _make_ride(driver, vehicle, seats_total=2)
    passenger = User.objects.create_user(phone_number="+21630000041", password="x")
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)

    booking = Booking.objects.get(pk=_create_booking(passenger_client, ride, seats=1).data["id"])

    # The passenger cannot accept or reject — driver-only actions.
    assert _transition(passenger_client, booking, "accept").status_code == 403
    assert _transition(passenger_client, booking, "reject").status_code == 403
    assert Booking.objects.get(pk=booking.id).status == "REQUESTED"

    # The driver cannot use the passenger's cancel action.
    assert _transition(driver_client, booking, "accept").status_code == 200
    assert _transition(driver_client, booking, "cancel_passenger").status_code == 403
    assert Booking.objects.get(pk=booking.id).status == "ACCEPTED"

    # But the passenger can cancel their own booking.
    assert _transition(passenger_client, booking, "cancel_passenger").status_code == 200


@pytest.mark.django_db
def test_cancelling_after_acceptance_releases_seats_and_reopens_ride():
    driver, vehicle = _make_driver("+21630000005")
    ride = _make_ride(driver, vehicle, seats_total=2)
    passenger = User.objects.create_user(phone_number="+21630000006", password="x")
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)

    booking = Booking.objects.get(pk=_create_booking(passenger_client, ride, seats=2).data["id"])
    assert _transition(driver_client, booking, "accept").status_code == 200
    ride.refresh_from_db()
    assert (ride.seats_available, ride.status) == (0, "FULL")

    assert _transition(driver_client, booking, "cancel_driver").status_code == 200
    ride.refresh_from_db()
    assert (ride.seats_available, ride.status) == (2, "OPEN")


@pytest.mark.django_db
def test_rejecting_from_requested_never_touches_seats():
    driver, vehicle = _make_driver("+21630000007")
    ride = _make_ride(driver, vehicle, seats_total=2)
    passenger = User.objects.create_user(phone_number="+21630000008", password="x")
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)

    booking = Booking.objects.get(pk=_create_booking(passenger_client, ride, seats=2).data["id"])
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    response = _transition(driver_client, booking, "reject")
    assert response.status_code == 200
    ride.refresh_from_db()
    assert ride.seats_available == 2


@pytest.mark.django_db
def test_overbooking_is_rejected_at_acceptance():
    driver, vehicle = _make_driver("+21630000009")
    ride = _make_ride(driver, vehicle, seats_total=1)
    alice = User.objects.create_user(phone_number="+21630000010", password="x")
    bob = User.objects.create_user(phone_number="+21630000011", password="x")
    alice_client, bob_client = APIClient(), APIClient()
    alice_client.force_authenticate(alice)
    bob_client.force_authenticate(bob)

    first = Booking.objects.get(pk=_create_booking(alice_client, ride, seats=1).data["id"])
    second = Booking.objects.get(pk=_create_booking(bob_client, ride, seats=1).data["id"])

    assert _transition(alice_client, first, "accept").status_code == 403  # only the driver can accept
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    assert _transition(driver_client, first, "accept").status_code == 200
    response = _transition(driver_client, second, "accept")
    assert response.status_code == 409
    second.refresh_from_db()
    assert second.status == "REQUESTED"
    ride.refresh_from_db()
    assert ride.seats_available == 0


@pytest.mark.django_db
def test_booking_rules_are_enforced():
    driver, vehicle = _make_driver("+21630000012")
    ride = _make_ride(driver, vehicle, seats_total=2)
    passenger = User.objects.create_user(phone_number="+21630000013", password="x")
    client = APIClient()
    client.force_authenticate(passenger)

    # A driver cannot book their own ride.
    own_client = APIClient()
    own_client.force_authenticate(driver)
    assert _create_booking(own_client, ride, seats=1).status_code == 400

    # Cannot ask for more seats than are available.
    assert _create_booking(client, ride, seats=3).status_code == 400

    # Cannot book a closed ride.
    ride.status = Ride.Status.CANCELLED
    ride.save(update_fields=["status"])
    assert _create_booking(client, ride, seats=1).status_code == 400


@pytest.mark.django_db
def test_near_search_returns_open_rides_by_distance():
    driver, vehicle = _make_driver("+21630000014")
    near_ride = _make_ride(driver, vehicle, seats_total=2)
    # A second ride far away (Gabès ~ 300km) that must not appear in a 5km search.
    far_ride = ride_services.publish_ride(
        driver=driver, vehicle=vehicle,
        origin=Point(10.10, 33.88, srid=4326), destination=DROPOFF,
        origin_label="Gabes", destination_label="Sousse",
        departure_at=timezone.now() + timedelta(days=1),
        seats_total=2, price_per_seat=Decimal("20.000"),
    )

    client = APIClient()
    client.force_authenticate(User.objects.create_user(phone_number="+21630000015", password="x"))
    response = client.get("/api/v1/rides/near/", {"lat": 36.80, "lng": 10.18, "radius_km": 5})
    assert response.status_code == 200
    ids = [row["id"] for row in response.data["results"]]
    assert str(near_ride.id) in ids
    assert str(far_ride.id) not in ids
    near_row = next(row for row in response.data["results"] if row["id"] == str(near_ride.id))
    assert near_row["distance_km"] is not None
