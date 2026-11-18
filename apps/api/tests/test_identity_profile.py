import pytest
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.users.models import Profile, User
from apps.users.serializers import UserSerializer

pytestmark = pytest.mark.django_db


def test_email_only_manager_normalizes_creates_empty_profile_without_inventing_proof():
    user = User.objects.create_user(email="  Identity@Example.com  ")
    assert user.email == "identity@example.com"
    assert user.phone_number is None
    assert str(user) == user.email
    assert user.profile.full_name == ""
    assert user.phone_verified_at is None
    assert user.email_verified_at is None
    assert not user.has_usable_password()
    assert not UserSerializer(user).data["onboarding_complete"]
    # Empty emails are intentionally not unique; NULL phone allows email-only users.
    User.objects.create_user(email="another@example.com")
    phone_user = User.objects.create_user("+21698002001")
    User.objects.create_user("+21698002002")
    assert str(phone_user) == phone_user.phone_number
    assert phone_user.phone_verified_at is None


def test_manager_rejects_no_contact_and_supports_email_only_superuser():
    with pytest.raises(ValueError):
        User.objects.create_user(email="  ", phone_number=" ")
    user = User.objects.create_superuser(email="Admin@Example.com", password="test-only")
    assert user.is_staff and user.is_superuser
    assert user.check_password("test-only")
    with pytest.raises(ValueError):
        User.objects.create_superuser(email="other@example.com", is_staff=False)


def test_case_insensitive_email_and_nonempty_contact_constraints_are_real():
    user = User.objects.create_user(email="identity@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("+21698002003", email="IDENTITY@example.com")
    for phone, email in [(None, ""), ("", ""), ("   ", ""), (None, "  ")]:
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=user.pk).update(phone_number=phone, email=email)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(email=" Upper@Example.com ")
    user.refresh_from_db()
    assert user.email == "identity@example.com"


def test_model_save_normalizes_email():
    user = User.objects.create_user("+21698002004")
    user.email = "  Mixed@Example.COM  "
    user.save()
    user.refresh_from_db()
    assert user.email == "mixed@example.com"


@pytest.mark.parametrize("legacy_missing", [False, True])
def test_profile_nested_write_readonly_contacts_and_onboarding(legacy_missing):
    user = User.objects.create_user("+21698002005")
    if legacy_missing:
        Profile.objects.filter(user=user).delete()
        # Reload to discard the reverse relation cache populated by the manager.
        user = User.objects.get(pk=user.pk)
    client = APIClient()
    client.force_authenticate(user)
    initial = client.get("/api/v1/users/me/")
    assert initial.status_code == 200
    assert not initial.data["onboarding_complete"]
    assert initial.data["profile"] is None if legacy_missing else initial.data["profile"]["full_name"] == ""
    response = client.patch(
        "/api/v1/users/me/",
        {
            "profile": {
                "full_name": "  Synthetic Passenger  ",
                "bio": "Hello",
                "avatar_key": "forged",
                "average_rating": "5.00",
            },
            "phone_number": "+21698002999",
            "email": "forged@example.com",
            "phone_verified_at": "2026-01-01T00:00:00Z",
            "email_verified_at": "2026-01-01T00:00:00Z",
            "role": "DRIVER",
            "is_verified": True,
            "onboarding_complete": True,
            "preferred_language": "ar",
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.data["onboarding_complete"]
    user.refresh_from_db()
    assert user.profile.full_name == "Synthetic Passenger"
    assert user.profile.bio == "Hello"
    assert user.profile.avatar_key == ""
    assert user.profile.average_rating == 0
    assert user.phone_number == "+21698002005"
    assert user.email == ""
    assert user.phone_verified_at is None and user.email_verified_at is None
    assert user.role == "PASSENGER" and not user.is_verified
    assert user.preferred_language == "ar"
    response = client.patch("/api/v1/users/me/", {"profile": {"full_name": "  "}}, format="json")
    assert response.status_code == 200 and not response.data["onboarding_complete"]
    assert response.data["profile"]["bio"] == "Hello"


def test_invalid_nested_profile_does_not_partially_update_user():
    user = User.objects.create_user(email="profile@example.com")
    client = APIClient()
    client.force_authenticate(user)
    response = client.patch(
        "/api/v1/users/me/",
        {
            "preferred_language": "ar",
            "profile": {"full_name": "x" * 151, "bio": "x" * 501},
        },
        format="json",
    )
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.preferred_language == "fr" and user.profile.full_name == ""
    assert client.patch("/api/v1/users/me/", {"profile": None}, format="json").status_code == 400


def test_nested_serializer_create_uses_atomic_manager():
    serializer = UserSerializer(data={"profile": {"full_name": "Synthetic", "bio": "Test"}})
    serializer.is_valid(raise_exception=True)
    user = serializer.save(email="create@example.com")
    assert user.profile.full_name == "Synthetic" and user.profile.bio == "Test"
    assert Profile.objects.filter(user=user).count() == 1
