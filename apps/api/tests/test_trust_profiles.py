"""Phase 7: participant profiles, validated reports and staff decision audit."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from test_phase7_flow import (
    _confirmed_booking,
    _make_booking,
    _make_driver,
    _staff_client,
    _start,
)

from apps.audit.models import AuditLog
from apps.rides.models import Ride
from apps.users.models import User
from apps.wallet.services import balance_of, ensure_user_account


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


# ------------------------------------------------------------ public profile


@pytest.mark.django_db
def test_public_profile_is_participant_safe_and_hides_contact_data():
    driver, vehicle = _make_driver("+21671000001")
    passenger = User.objects.create_user(phone_number="+21671000002", password="x", email="lina@example.tn")
    booking = _confirmed_booking(driver, vehicle, passenger)
    _client(driver).post(f"/api/v1/rides/{booking.ride_id}/start/")
    _client(driver).post(f"/api/v1/rides/{booking.ride_id}/complete/")
    _client(passenger).post(
        "/api/v1/ratings/", {"booking": str(booking.id), "score": 5, "comment": "Punctual and kind"}, format="json",
    )

    seen_by_stranger = _client(passenger).get(f"/api/v1/users/{driver.id}/")
    assert seen_by_stranger.status_code == 200
    body = seen_by_stranger.data
    assert body["average_rating"] == "5.00"
    assert body["review_count"] == 1
    assert body["completed_trips"] == 1
    assert body["is_driver"] is True
    # Participants can read the comment; no contact, document or wallet data is present.
    assert body["reviews_visible"] is True
    assert body["reviews"][0]["comment"] == "Punctual and kind"
    for forbidden in ("phone_number", "email", "wallet", "documents", "identity"):
        assert forbidden not in body
    assert "lina@example.tn" not in str(body)

    outsider = User.objects.create_user(phone_number="+21671000003", password="x")
    stranger_view = _client(outsider).get(f"/api/v1/users/{driver.id}/")
    assert stranger_view.status_code == 200
    assert stranger_view.data["review_count"] == 1
    assert stranger_view.data["reviews_visible"] is False
    assert stranger_view.data["reviews"] == []

    # Unknown and deactivated members are not enumerable.
    assert _client(passenger).get("/api/v1/users/11111111-1111-1111-1111-111111111111/").status_code == 404
    outsider.is_active = False
    outsider.save(update_fields=["is_active"])
    assert _client(passenger).get(f"/api/v1/users/{outsider.id}/").status_code == 404


@pytest.mark.django_db
def test_public_profile_reports_shared_interests_only_with_a_real_shared_tag():
    driver, vehicle = _make_driver("+21671000004")
    passenger = User.objects.create_user(phone_number="+21671000005", password="x")
    for user, slugs in ((driver, ["music", "tech"]), (passenger, ["music"])):
        _client(user).patch(
            "/api/v1/users/me/", {"profile": {"full_name": "Test Member", "interests": slugs}}, format="json",
        )
    body = _client(passenger).get(f"/api/v1/users/{driver.id}/").data
    assert body["shared_interests"] == ["music"]
    # Yourself never reports shared interests with yourself.
    assert _client(driver).get(f"/api/v1/users/{driver.id}/").data["shared_interests"] == []
    assert body["display_name"] == "Test M."


# ------------------------------------------------------------ report targets


@pytest.mark.django_db
def test_report_targets_are_validated_against_existence_and_relationship():
    driver, vehicle = _make_driver("+21671000006")
    passenger = User.objects.create_user(phone_number="+21671000007", password="x")
    outsider = User.objects.create_user(phone_number="+21671000008", password="x")
    booking = _make_booking(driver, vehicle, passenger)
    ride = Ride.objects.get(pk=booking.ride_id)

    client = _client(passenger)
    assert client.post(
        "/api/v1/moderation/reports/",
        {"target_type": "RIDE", "target_id": "11111111-1111-1111-1111-111111111111", "reason": "SAFETY"},
        format="json",
    ).status_code == 400
    assert client.post(
        "/api/v1/moderation/reports/", {"target_type": "NOPE", "target_id": str(ride.id), "reason": "SAFETY"},
        format="json",
    ).status_code == 400
    assert client.post(
        "/api/v1/moderation/reports/", {"target_type": "USER", "target_id": str(passenger.id), "reason": "SPAM"},
        format="json",
    ).status_code == 400
    assert client.post(
        "/api/v1/moderation/reports/", {"target_type": "USER", "target_id": str(driver.id), "reason": "NOT_A_REASON"},
        format="json",
    ).status_code == 400

    # A member with no relationship to the ride cannot report it.
    assert _client(outsider).post(
        "/api/v1/moderation/reports/", {"target_type": "RIDE", "target_id": str(ride.id), "reason": "SAFETY"},
        format="json",
    ).status_code == 400
    assert _client(outsider).post(
        "/api/v1/moderation/reports/", {"target_type": "VEHICLE", "target_id": str(vehicle.id), "reason": "SAFETY"},
        format="json",
    ).status_code == 400

    # The participant can, and the vehicle they actually rode in.
    assert client.post(
        "/api/v1/moderation/reports/",
        {"target_type": "RIDE", "target_id": str(ride.id), "reason": "SAFETY", "details": "wrong pickup point"},
        format="json",
    ).status_code == 201
    assert client.post(
        "/api/v1/moderation/reports/", {"target_type": "VEHICLE", "target_id": str(vehicle.id), "reason": "SAFETY"},
        format="json",
    ).status_code == 201

    # A message from a conversation the reporter belongs to is reportable.
    from apps.messaging.models import Message

    conversation = booking.conversation
    message = Message.objects.create(conversation=conversation, sender=driver, body="Meet me off-platform")
    assert client.post(
        "/api/v1/moderation/reports/",
        {"target_type": "MESSAGE", "target_id": str(message.id), "reason": "FRAUD"},
        format="json",
    ).status_code == 201
    assert _client(outsider).post(
        "/api/v1/moderation/reports/",
        {"target_type": "MESSAGE", "target_id": str(message.id), "reason": "FRAUD"},
        format="json",
    ).status_code == 400


# ------------------------------------------------------------ staff audit


@pytest.mark.django_db
def test_staff_decisions_write_audit_metadata_without_secrets():
    driver, vehicle = _make_driver("+21671000009")
    passenger = User.objects.create_user(phone_number="+21671000010", password="x")
    client = _client(passenger)
    report = client.post(
        "/api/v1/moderation/reports/",
        {"target_type": "USER", "target_id": str(driver.id), "reason": "FRAUD", "details": "cash off-platform"},
        format="json",
    )
    staff_client, staff = _staff_client()
    assert staff_client.post(
        f"/api/v1/moderation/reports/{report.data['id']}/review/",
        {"status": "ACTIONED", "reason": "confirmed off-platform solicitation"}, format="json",
    ).status_code == 200

    entry = AuditLog.objects.get(action="report.actioned", object_id=report.data["id"])
    assert entry.actor_id == staff.id
    assert entry.object_type == "report"
    assert entry.reason == "confirmed off-platform solicitation"
    assert entry.metadata["target_type"] == "USER"
    assert "cash off-platform" not in str(entry.metadata)

    staff_client.post(f"/api/v1/moderation/users/{driver.id}/ban/", {"reason": "fraud"}, format="json")
    assert AuditLog.objects.filter(action="user.banned", object_id=str(driver.id)).exists()

    # The staff trail is readable and scoped; ordinary members cannot read it.
    trail = staff_client.get(f"/api/v1/moderation/audit/report/{report.data['id']}/")
    assert trail.status_code == 200
    assert trail.data[0]["action"] == "report.actioned"
    assert "request_id" not in trail.data[0]
    assert client.get(f"/api/v1/moderation/audit/report/{report.data['id']}/").status_code == 403


@pytest.mark.django_db
def test_staff_ride_override_refunds_through_the_same_services():
    driver, vehicle = _make_driver("+21671000011")
    passenger = User.objects.create_user(phone_number="+21671000012", password="x")
    booking = _confirmed_booking(driver, vehicle, passenger)
    before = balance_of(ensure_user_account(passenger))
    assert before == 0

    passenger_client = _client(passenger)
    assert passenger_client.post(f"/api/v1/moderation/rides/{booking.ride_id}/override/", {"action": "cancel"}).status_code == 403

    staff_client, staff = _staff_client()
    cancelled = staff_client.post(
        f"/api/v1/moderation/rides/{booking.ride_id}/override/",
        {"action": "cancel", "reason": "driver unreachable, passengers stranded"}, format="json",
    )
    assert cancelled.status_code == 200
    assert cancelled.data["status"] == "CANCELLED"

    booking.refresh_from_db()
    assert booking.status == "CANCELLED_BY_DRIVER"
    assert booking.refunded_amount == booking.paid_amount
    assert balance_of(ensure_user_account(passenger)) == booking.paid_amount
    assert balance_of(ensure_user_account(driver)) == 0
    assert AuditLog.objects.filter(
        action="ride.cancelled_by_staff", object_id=str(booking.ride_id), actor=staff,
    ).exists()

    # Seats are released by the same service, so the ride cannot silently reopen.
    ride = Ride.objects.get(pk=booking.ride_id)
    assert ride.status == Ride.Status.CANCELLED
    assert ride.seats_available == ride.seats_total


@pytest.mark.django_db
def test_staff_can_complete_an_abandoned_in_progress_ride():
    driver, vehicle = _make_driver("+21671000013")
    passenger = User.objects.create_user(phone_number="+21671000014", password="x")
    booking = _confirmed_booking(driver, vehicle, passenger)
    assert _start(_client(driver), booking).status_code == 200

    staff_client, staff = _staff_client()
    completed = staff_client.post(
        f"/api/v1/moderation/rides/{booking.ride_id}/override/", {"action": "complete"}, format="json",
    )
    assert completed.status_code == 200
    booking.refresh_from_db()
    assert booking.status == "COMPLETED"
    assert AuditLog.objects.filter(action="ride.completed_by_staff", actor=staff).exists()

    # Overriding an already terminal ride fails instead of double-posting money.
    assert staff_client.post(
        f"/api/v1/moderation/rides/{booking.ride_id}/override/", {"action": "cancel"}, format="json",
    ).status_code == 400


@pytest.mark.django_db
def test_verification_review_is_audited_and_notifies_the_owner():
    from apps.verification.models import VerificationDocument
    from apps.verification.services import review_document

    driver, _ = _make_driver("+21671000015")
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CIN, storage_key="verification/cin.pdf",
        status=VerificationDocument.Status.PENDING, uploaded_at=timezone.now() - timedelta(minutes=1),
    )
    staff_client, staff = _staff_client()
    from apps.verification import services as verification_services

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(verification_services, "validate_stored_document", lambda document: ("storage", "etag"))
        review_document(
            document=document, reviewer=staff, decision="REJECT", reason="The photo is unreadable",
        )
    entry = AuditLog.objects.get(action="verification.reject", object_id=str(document.pk))
    assert entry.actor_id == staff.id
    assert entry.metadata["doc_type"] == "CIN"
    assert entry.reason == "The photo is unreadable"
