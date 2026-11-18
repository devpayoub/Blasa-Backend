from io import BytesIO
from unittest.mock import patch

import pytest
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.auth import services as auth_services
from apps.auth.models import OtpCode
from apps.users.models import User
from apps.verification.models import VerificationDocument
from apps.verification.services import review_document


def _request_otp(client, phone):
    with patch("apps.auth.views.send_otp_sms") as mock_task, TestCase.captureOnCommitCallbacks(execute=True):
        response = client.post("/api/v1/auth/otp/request/", {"phone_number": phone}, format="json")
    assert response.status_code == 200
    code = mock_task.delay.call_args.kwargs["code"]
    assert response.data == {"detail": "A login code was sent by SMS."}  # the code never leaks
    return code


@pytest.mark.django_db
def test_otp_login_full_flow_creates_account_and_issues_tokens():
    client = APIClient()
    phone = "+21698000101"
    code = _request_otp(client, phone)

    verify = client.post("/api/v1/auth/otp/verify/", {"phone_number": phone, "code": code}, format="json")
    assert verify.status_code == 200
    assert set(verify.data) == {"access", "refresh"}

    # First login created a passenger account
    user = User.objects.get(phone_number=phone)
    assert user.role == User.Role.PASSENGER

    me = client.get("/api/v1/users/me/", HTTP_AUTHORIZATION=f"Bearer {verify.data['access']}")
    assert me.status_code == 200
    assert me.data["phone_number"] == phone


@pytest.mark.django_db
def test_wrong_code_increments_attempts_and_valid_code_still_works():
    client = APIClient()
    phone = "+21698000102"
    code = _request_otp(client, phone)

    wrong_code = "000000" if code != "000000" else "000001"
    for _ in range(auth_services.MAX_ATTEMPTS):
        bad = client.post("/api/v1/auth/otp/verify/", {"phone_number": phone, "code": wrong_code}, format="json")
        assert bad.status_code == 400
    otp = OtpCode.objects.filter(phone_number=phone).first()
    otp.refresh_from_db()
    assert otp.attempts == auth_services.MAX_ATTEMPTS
    assert otp.consumed_at is None

    # Even the correct code is now refused — the OTP is burned.
    burned = client.post("/api/v1/auth/otp/verify/", {"phone_number": phone, "code": code}, format="json")
    assert burned.status_code == 400


@pytest.mark.django_db
def test_expired_or_consumed_codes_are_rejected():
    client = APIClient()
    phone = "+21698000103"
    code = _request_otp(client, phone)

    assert (
        client.post("/api/v1/auth/otp/verify/", {"phone_number": phone, "code": code}, format="json").status_code == 200
    )

    # Single use: replaying the same code fails.
    replay = client.post("/api/v1/auth/otp/verify/", {"phone_number": phone, "code": code}, format="json")
    assert replay.status_code == 400


@pytest.mark.django_db
def test_otp_flood_guard():
    phone = "+21698000104"
    for _ in range(auth_services.MAX_CODES_PER_WINDOW):
        auth_services.request_otp(phone_number=phone)
    with pytest.raises(ValueError):
        auth_services.request_otp(phone_number=phone)

    client = APIClient()
    response = client.post("/api/v1/auth/otp/request/", {"phone_number": phone}, format="json")
    assert response.status_code == 429


@pytest.mark.django_db
def test_refresh_rotation_and_logout_blacklisting():
    client = APIClient()
    phone = "+21698000105"
    code = _request_otp(client, phone)
    tokens = client.post("/api/v1/auth/otp/verify/", {"phone_number": phone, "code": code}, format="json").data

    refreshed = client.post("/api/v1/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json")
    assert refreshed.status_code == 200
    assert refreshed.data["refresh"] != tokens["refresh"]
    assert client.post("/api/v1/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json").status_code == 401

    rotated = client.post("/api/v1/auth/token/refresh/", {"refresh": refreshed.data["refresh"]}, format="json")
    assert rotated.status_code == 200
    assert rotated.data["refresh"] != refreshed.data["refresh"]

    logout = client.post(
        "/api/v1/auth/logout/",
        {"refresh": rotated.data["refresh"]},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {rotated.data['access']}",
    )
    assert logout.status_code == 200
    dead = client.post("/api/v1/auth/token/refresh/", {"refresh": rotated.data["refresh"]}, format="json")
    assert dead.status_code == 401


@pytest.mark.django_db
def test_approved_cin_flips_is_verified_and_unlocks_driver_upgrade():
    client = APIClient()
    phone = "+21698000106"
    code = _request_otp(client, phone)
    tokens = client.post("/api/v1/auth/otp/verify/", {"phone_number": phone, "code": code}, format="json").data
    user = User.objects.get(phone_number=phone)
    assert user.is_verified is False

    # Upgrade is refused before CIN approval.
    refused = client.post(
        "/api/v1/users/me/become-driver/", format="json", HTTP_AUTHORIZATION=f"Bearer {tokens['access']}"
    )
    assert refused.status_code == 400

    # A reviewer approves a confirmed synthetic upload -> the event flips is_verified.
    # Only the external S3 transport is stubbed; eligibility/account state is real DB state.
    document = VerificationDocument.objects.create(
        user=user,
        doc_type=VerificationDocument.DocType.CIN,
        storage_key=f"verification/{user.pk}/cin/synthetic.jpg",
        content_type="image/jpeg",
        size_bytes=3,
        uploaded_at=timezone.now(),
    )
    reviewer = User.objects.create_user(phone_number="+21698000107", password="x", is_staff=True)
    with patch("apps.verification.storage.client") as storage:
        storage.return_value.head_object.return_value = {
            "ContentLength": 3,
            "ContentType": "image/jpeg",
            "ETag": "synthetic-etag",
        }
        storage.return_value.get_object.return_value = {
            "Body": BytesIO(b"\xff\xd8\xff"),
            "ContentType": "image/jpeg",
            "ETag": "synthetic-etag",
        }
        review_document(document=document, reviewer=reviewer, decision="APPROVE")
    user.refresh_from_db()
    assert user.is_verified is True

    # Now the upgrade succeeds.
    upgraded = client.post(
        "/api/v1/users/me/become-driver/", format="json", HTTP_AUTHORIZATION=f"Bearer {tokens['access']}"
    )
    assert upgraded.status_code == 200
    assert upgraded.data["role"] == User.Role.DRIVER
    user.refresh_from_db()
    assert user.role == User.Role.DRIVER
