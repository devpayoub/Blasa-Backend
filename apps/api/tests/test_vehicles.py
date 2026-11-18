from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from apps.rides import services as ride_services
from apps.rides.models import Ride
from apps.users.models import User
from apps.vehicles.models import Vehicle
from apps.verification.models import VerificationDocument
from apps.verification.services import confirm_upload, review_document, revoke_document

PDF = b"%PDF-1.7 synthetic registration"


@pytest.fixture(autouse=True)
def enable_synthetic_uploads(settings):
    settings.IDENTITY_DOCUMENT_UPLOADS_ENABLED = True


def _approve(doc, reviewer=None):
    reviewer = reviewer or User.objects.create_user(phone_number="+21620000999", is_staff=True)
    doc.storage_key = f"verification/{doc.user_id}/carte_grise/synthetic.pdf"
    doc.content_type = "application/pdf"
    doc.size_bytes = len(PDF)
    doc.save(update_fields=["storage_key", "content_type", "size_bytes"])
    with patch("apps.verification.storage.client") as mock_s3:
        mock_s3.return_value.head_object.return_value = {
            "ContentLength": len(PDF), "ContentType": "application/pdf", "ETag": '"synthetic"',
        }
        mock_s3.return_value.get_object.side_effect = lambda **_: {
            "Body": BytesIO(PDF), "ContentType": "application/pdf", "ETag": '"synthetic"',
        }
        confirmed = confirm_upload(document=doc, actor=doc.user)
        return review_document(document=confirmed, reviewer=reviewer, decision="APPROVE")


@pytest.mark.django_db
def test_only_drivers_can_register_vehicles():
    passenger = User.objects.create_user(phone_number="+21620000010", password="x")
    client = APIClient()
    client.force_authenticate(passenger)

    response = client.post(
        "/api/v1/vehicles/",
        {"make": "Dacia", "model": "Logan", "plate_number": "123 TU 4567", "seats": 3},
        format="json",
    )
    assert response.status_code == 400
    assert Vehicle.objects.count() == 0


@pytest.mark.django_db
def test_driver_registers_vehicle_and_verification_state_is_derived():
    driver = User.objects.create_user(phone_number="+21620000011", password="x", role=User.Role.DRIVER)
    client = APIClient()
    client.force_authenticate(driver)

    with patch("apps.verification.storage.client") as mock_s3:
        mock_s3.return_value.generate_presigned_url.return_value = "https://s3.test/signed-put"
        doc_resp = client.post(
            "/api/v1/verification/documents/upload-url/",
            {"doc_type": "CARTE_GRISE", "file_name": "carte.pdf", "content_type": "application/pdf", "size_bytes": len(PDF)},
            format="json",
        )
    assert doc_resp.status_code == 201
    document = VerificationDocument.objects.get(pk=doc_resp.data["document_id"])

    response = client.post(
        "/api/v1/vehicles/",
        {"make": "Dacia", "model": "Logan", "plate_number": "123 TU 4567", "seats": 3},
        format="json",
    )
    assert response.status_code == 201
    vehicle = Vehicle.objects.get(pk=response.data["id"])
    assert vehicle.is_verified is False

    # Attach a pending Carte Grise: still not verified.
    attach = client.patch(f"/api/v1/vehicles/{vehicle.id}/", {"carte_grise": str(document.id)}, format="json")
    assert attach.status_code == 200
    assert attach.data["is_verified"] is False

    # Approve the document: the vehicle becomes verified and bookable, no extra state to flip.
    _approve(document)
    vehicle.refresh_from_db()
    assert vehicle.is_verified is True
    assert vehicle.is_bookable is True


@pytest.mark.django_db
def test_carte_grise_must_belong_to_the_driver():
    owner = User.objects.create_user(phone_number="+21620000012", password="x", role=User.Role.DRIVER)
    impostor = User.objects.create_user(phone_number="+21620000013", password="x", role=User.Role.DRIVER)
    document = VerificationDocument.objects.create(
        user=owner, doc_type=VerificationDocument.DocType.CARTE_GRISE, storage_key="verification/x/1.pdf"
    )

    client = APIClient()
    client.force_authenticate(impostor)
    response = client.post(
        "/api/v1/vehicles/",
        {
            "make": "Kia", "model": "Picanto", "plate_number": "999 TU 1111",
            "seats": 3, "carte_grise": str(document.id),
        },
        format="json",
    )
    assert response.status_code == 400
    assert Vehicle.objects.count() == 0


@pytest.mark.django_db
def test_verified_vehicle_is_required_for_publishing_prep():
    """Phase 2 gate: ride publishing requires a bookable vehicle."""
    driver = User.objects.create_user(phone_number="+21620000014", password="x", role=User.Role.DRIVER)
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE, storage_key="verification/x/2.pdf"
    )
    unverified = Vehicle.objects.create(
        driver=driver, make="Dacia", model="Logan", plate_number="555 TU 2222", seats=4, carte_grise=document
    )
    assert unverified.is_bookable is False

    _approve(document)
    unverified.refresh_from_db()
    assert unverified.is_bookable is True

    ride = ride_services.publish_ride(
        driver=driver,
        vehicle=unverified,
        origin=Point(10.18, 36.80, srid=4326),
        destination=Point(10.64, 35.83, srid=4326),
        origin_label="Tunis",
        destination_label="Sousse",
        departure_at=timezone.now() + timedelta(days=1),
        seats_total=3,
        price_per_seat=Decimal("12.000"),
    )
    assert ride.status == Ride.Status.OPEN


@pytest.mark.django_db
def test_publish_api_returns_the_persisted_ride():
    driver = User.objects.create_user(phone_number="+21620000015", role=User.Role.DRIVER)
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE,
        storage_key=f"verification/{driver.id}/carte_grise/synthetic.pdf",
    )
    _approve(document)
    vehicle = Vehicle.objects.create(
        driver=driver, make="Dacia", model="Logan", plate_number="556 TU 2222", seats=4,
        carte_grise=document,
    )
    client = APIClient()
    client.force_authenticate(driver)
    response = client.post(
        "/api/v1/rides/",
        {
            "vehicle": str(vehicle.id),
            "origin": {"type": "Point", "coordinates": [10.18, 36.80]},
            "destination": {"type": "Point", "coordinates": [10.64, 35.83]},
            "origin_label": "Tunis", "destination_label": "Sousse",
            "departure_at": (timezone.now() + timedelta(days=1)).isoformat(),
            "seats_total": 3, "price_per_seat": "12.000",
        },
        format="json",
    )
    assert response.status_code == 201
    ride = Ride.objects.get(pk=response.data["id"])
    assert str(response.data["driver"]) == str(driver.id)
    assert response.data["seats_available"] == ride.seats_available == 3
    assert response.data["status"] == ride.status == Ride.Status.OPEN
    assert response.data["price_per_seat"] == "12.000"
    assert client.get(f"/api/v1/rides/{ride.id}/").status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["passenger", "unverified_vehicle", "foreign_vehicle", "past_departure"])
def test_publish_api_maps_domain_rejections_to_validation_errors(failure):
    driver = User.objects.create_user(phone_number="+21620000016", role=User.Role.DRIVER)
    actor = driver
    if failure in {"passenger", "foreign_vehicle"}:
        actor = User.objects.create_user(
            phone_number="+21620000017",
            role=User.Role.PASSENGER if failure == "passenger" else User.Role.DRIVER,
        )
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE,
        storage_key=f"verification/{driver.id}/carte_grise/synthetic.pdf",
    )
    if failure != "unverified_vehicle":
        _approve(document)
    vehicle = Vehicle.objects.create(
        driver=driver, make="Dacia", model="Logan", plate_number="557 TU 2222", seats=4,
        carte_grise=document,
    )
    client = APIClient()
    client.force_authenticate(actor)
    departure = timezone.now() + timedelta(days=-1 if failure == "past_departure" else 1)
    response = client.post(
        "/api/v1/rides/",
        {
            "vehicle": str(vehicle.id),
            "origin": {"type": "Point", "coordinates": [10.18, 36.80]},
            "destination": {"type": "Point", "coordinates": [10.64, 35.83]},
            "origin_label": "Tunis", "destination_label": "Sousse",
            "departure_at": departure.isoformat(), "seats_total": 3, "price_per_seat": "12.000",
        },
        format="json",
    )
    assert response.status_code == 400
    assert Ride.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("bad_type", [False, True])
def test_invalid_carte_grise_patch_is_atomic(bad_type):
    driver = User.objects.create_user(phone_number="+21620000030", role=User.Role.DRIVER)
    other = User.objects.create_user(phone_number="+21620000031", role=User.Role.DRIVER)
    doc = VerificationDocument.objects.create(
        user=driver if bad_type else other,
        doc_type=VerificationDocument.DocType.CIN if bad_type else VerificationDocument.DocType.CARTE_GRISE,
        storage_key="verification/synthetic/invalid.pdf",
    )
    vehicle = Vehicle.objects.create(driver=driver, make="Kia", model="Rio", plate_number="100 TU 1000", seats=3)
    client = APIClient()
    client.force_authenticate(driver)
    response = client.patch(
        f"/api/v1/vehicles/{vehicle.pk}/",
        {"make": "Changed", "color": "blue", "seats": 1, "is_active": False, "carte_grise": str(doc.pk)},
        format="json",
    )
    assert response.status_code == 400
    vehicle.refresh_from_db()
    assert (vehicle.make, vehicle.color, vehicle.seats, vehicle.is_active, vehicle.carte_grise_id) == (
        "Kia", "", 3, True, None,
    )


@pytest.mark.django_db
def test_carte_grise_detach_and_activation_and_revocation():
    driver = User.objects.create_user(phone_number="+21620000032", role=User.Role.DRIVER)
    doc = VerificationDocument.objects.create(user=driver, doc_type="CARTE_GRISE", storage_key="synthetic.pdf")
    approved = _approve(doc)
    vehicle = Vehicle.objects.create(
        driver=driver, make="Kia", model="Rio", plate_number="101 TU 1000", seats=3, carte_grise=approved,
    )
    client = APIClient()
    client.force_authenticate(driver)
    url = f"/api/v1/vehicles/{vehicle.pk}/"
    inactive = client.patch(url, {"is_active": False}, format="json")
    assert inactive.status_code == 200
    assert inactive.data["is_verified"] is True and inactive.data["is_bookable"] is False
    active = client.patch(url, {"is_active": True}, format="json")
    assert active.status_code == 200 and active.data["is_bookable"] is True
    detached = client.patch(url, {"carte_grise": None}, format="json")
    assert detached.status_code == 200
    assert detached.data["carte_grise"] is None
    assert detached.data["is_verified"] is False and detached.data["is_bookable"] is False
    assert VerificationDocument.objects.filter(pk=approved.pk).exists()
    attached = client.patch(url, {"carte_grise": str(approved.pk)}, format="json")
    assert attached.status_code == 200 and attached.data["is_bookable"] is True
    reviewer = User.objects.get(phone_number="+21620000999")
    revoke_document(document=approved, reviewer=reviewer, reason="Registration withdrawn")
    vehicle.refresh_from_db()
    assert vehicle.is_verified is False and vehicle.is_bookable is False


@pytest.mark.django_db
@pytest.mark.parametrize("role,account_active", [("PASSENGER", True), ("BRAND", True), ("DRIVER", False)])
def test_invalid_role_or_inactive_account_cannot_patch_even_inactive_vehicle(role, account_active):
    driver = User.objects.create_user(phone_number="+21620000033", role=role, is_active=account_active)
    vehicle = Vehicle.objects.create(
        driver=driver, make="Kia", model="Rio", plate_number="102 TU 1000", seats=3, is_active=False,
    )
    client = APIClient()
    client.force_authenticate(driver)
    response = client.patch(f"/api/v1/vehicles/{vehicle.pk}/", {"make": "Changed", "is_active": True}, format="json")
    assert response.status_code == 400
    vehicle.refresh_from_db()
    assert vehicle.make == "Kia" and vehicle.is_active is False


@pytest.mark.django_db
def test_vehicle_queryset_is_owned_ordered_and_no_delete():
    driver = User.objects.create_user(phone_number="+21620000034", role=User.Role.DRIVER)
    other = User.objects.create_user(phone_number="+21620000035", role=User.Role.DRIVER)
    first = Vehicle.objects.create(driver=driver, make="Kia", model="Rio", plate_number="103 TU 1000", seats=3)
    second = Vehicle.objects.create(driver=driver, make="Kia", model="Rio", plate_number="104 TU 1000", seats=3)
    foreign = Vehicle.objects.create(driver=other, make="Kia", model="Rio", plate_number="105 TU 1000", seats=3)
    client = APIClient()
    client.force_authenticate(driver)
    listing = client.get("/api/v1/vehicles/")
    results = listing.data["results"] if isinstance(listing.data, dict) else listing.data
    assert [str(v["id"]) for v in results] == [str(second.pk), str(first.pk)]
    assert client.get(f"/api/v1/vehicles/{foreign.pk}/").status_code == 404
    assert client.patch(f"/api/v1/vehicles/{foreign.pk}/", {"make": "Changed"}, format="json").status_code == 404
    assert client.delete(f"/api/v1/vehicles/{first.pk}/").status_code == 405
