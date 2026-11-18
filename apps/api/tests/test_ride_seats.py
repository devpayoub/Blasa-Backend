from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.rides import services
from apps.rides.models import Ride
from apps.users.models import User
from apps.vehicles.models import Vehicle
from apps.verification.models import VerificationDocument


def _make_driver_and_bookable_vehicle(phone: str):
    driver = User.objects.create_user(phone_number=phone, password="x", role=User.Role.DRIVER)
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE, storage_key=f"verification/{phone}.pdf",
        status=VerificationDocument.Status.APPROVED,
    )
    vehicle = Vehicle.objects.create(
        driver=driver, make="Dacia", model="Logan", plate_number=f"{phone[-4:]} TU 0000",
        seats=4, carte_grise=document,
    )
    return driver, vehicle


def _publish_ride(driver, vehicle, seats_total=2):
    return services.publish_ride(
        driver=driver,
        vehicle=vehicle,
        origin=Point(10.18, 36.80, srid=4326),
        destination=Point(10.64, 35.83, srid=4326),
        origin_label="Tunis",
        destination_label="Sousse",
        departure_at=timezone.now() + timedelta(days=1),
        seats_total=seats_total,
        price_per_seat=Decimal("15.000"),
    )


@pytest.mark.django_db
def test_reserving_all_seats_marks_ride_full():
    driver, vehicle = _make_driver_and_bookable_vehicle("+21620000001")
    ride = _publish_ride(driver, vehicle, seats_total=2)
    ride = services.reserve_seats(ride_id=ride.id, seats=2)
    assert ride.status == Ride.Status.FULL
    assert ride.seats_available == 0


@pytest.mark.django_db
def test_releasing_seats_reopens_a_full_ride():
    driver, vehicle = _make_driver_and_bookable_vehicle("+21620000002")
    ride = _publish_ride(driver, vehicle, seats_total=2)
    services.reserve_seats(ride_id=ride.id, seats=2)
    ride = services.release_seats(ride_id=ride.id, seats=1)
    assert ride.status == Ride.Status.OPEN
    assert ride.seats_available == 1


@pytest.mark.django_db
def test_publish_ride_requires_driver_role_and_bookable_vehicle():
    passenger = User.objects.create_user(phone_number="+21620000003", password="x")
    driver, vehicle = _make_driver_and_bookable_vehicle("+21620000004")

    with pytest.raises(ValueError):
        _publish_ride(passenger, vehicle)

    vehicle.is_active = False
    vehicle.save(update_fields=["is_active"])
    with pytest.raises(ValueError):
        _publish_ride(driver, vehicle)

    vehicle.is_active = True
    vehicle.carte_grise = None
    vehicle.save(update_fields=["is_active", "carte_grise"])
    with pytest.raises(ValueError):
        _publish_ride(driver, vehicle)
