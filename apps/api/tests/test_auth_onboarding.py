from unittest.mock import patch

import pytest
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import User

AUTH = "/api/v1/auth/"


def _request_otp(client, phone):
    with patch("apps.auth.views.send_otp_sms") as mock_task, TestCase.captureOnCommitCallbacks(execute=True):
        response = client.post(AUTH + "otp/request/", {"phone_number": phone}, format="json")
    assert response.status_code == 200
    return mock_task.delay.call_args.kwargs["code"]


def _bearer(tokens):
    return {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}


@pytest.mark.django_db
def test_contact_check_distinguishes_new_and_registered_accounts():
    client = APIClient()

    new = client.post(AUTH + "contact/check/", {"contact": "+21698111001"}, format="json")
    assert new.status_code == 200
    assert new.data == {"channel": "PHONE", "exists": False, "has_password": False}

    User.objects.create_user(phone_number="+21698111002", password="correct-horse-9")
    existing = client.post(AUTH + "contact/check/", {"contact": "+21698111002"}, format="json")
    assert existing.data == {"channel": "PHONE", "exists": True, "has_password": True}

    email = client.post(AUTH + "contact/check/", {"contact": "Driver@Example.TN"}, format="json")
    assert email.data == {"channel": "EMAIL", "exists": False, "has_password": False}

    invalid = client.post(AUTH + "contact/check/", {"contact": "not-a-contact"}, format="json")
    assert invalid.status_code == 400
    assert invalid.data["error"]["code"] == "invalid"


@pytest.mark.django_db
def test_password_login_returns_tokens_and_hides_which_half_was_wrong():
    client = APIClient()
    User.objects.create_user(phone_number="+21698111003", password="correct-horse-9")

    ok = client.post(
        AUTH + "password/login/",
        {"contact": "+21698111003", "password": "correct-horse-9"},
        format="json",
    )
    assert ok.status_code == 200 and set(ok.data) == {"access", "refresh"}

    wrong = client.post(
        AUTH + "password/login/",
        {"contact": "+21698111003", "password": "wrong-password"},
        format="json",
    )
    unknown = client.post(
        AUTH + "password/login/",
        {"contact": "+21698111099", "password": "wrong-password"},
        format="json",
    )
    assert wrong.status_code == unknown.status_code == 400
    assert wrong.data == unknown.data


@pytest.mark.django_db
def test_otp_then_complete_registration_sets_name_password_and_role_once():
    client = APIClient()
    phone = "+21698111004"
    code = _request_otp(client, phone)

    verify = client.post(AUTH + "otp/verify/", {"phone_number": phone, "code": code}, format="json")
    assert verify.status_code == 200
    assert User.objects.get(phone_number=phone).has_usable_password() is False

    complete = client.post(
        AUTH + "register/complete/",
        {"full_name": "  Amine   Trabelsi ", "password": "a-strong-pass-42", "role": "DRIVER"},
        format="json",
        **_bearer(verify.data),
    )
    assert complete.status_code == 200
    assert complete.data["profile"]["full_name"] == "Amine Trabelsi"
    assert complete.data["role"] == "DRIVER"
    assert complete.data["is_verified"] is False

    user = User.objects.get(phone_number=phone)
    assert user.check_password("a-strong-pass-42")

    # Replaying the completion must not overwrite the stored credential.
    replay = client.post(
        AUTH + "register/complete/",
        {"full_name": "Someone Else", "password": "another-pass-77", "role": "PASSENGER"},
        format="json",
        **_bearer(verify.data),
    )
    assert replay.status_code == 400
    assert user.__class__.objects.get(pk=user.pk).check_password("a-strong-pass-42")

    login = client.post(
        AUTH + "password/login/",
        {"contact": phone, "password": "a-strong-pass-42"},
        format="json",
    )
    assert login.status_code == 200


@pytest.mark.django_db
def test_complete_registration_rejects_weak_passwords_and_unknown_roles():
    client = APIClient()
    phone = "+21698111005"
    code = _request_otp(client, phone)
    tokens = client.post(AUTH + "otp/verify/", {"phone_number": phone, "code": code}, format="json").data

    weak = client.post(
        AUTH + "register/complete/",
        {"full_name": "Test User", "password": "1234", "role": "PASSENGER"},
        format="json",
        **_bearer(tokens),
    )
    assert weak.status_code == 400

    brand = client.post(
        AUTH + "register/complete/",
        {"full_name": "Test User", "password": "a-strong-pass-42", "role": "BRAND"},
        format="json",
        **_bearer(tokens),
    )
    assert brand.status_code == 400

    # The account stays without a password so the user can retry.
    assert User.objects.get(phone_number=phone).has_usable_password() is False
