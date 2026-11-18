import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core import mail
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.auth.models import ContactOtp, OtpCode
from apps.auth.services import (
    CODE_TTL,
    INVALID_CODE,
    MAX_ATTEMPTS,
    MAX_CODES_PER_WINDOW,
    _hash,
    request_contact_otp,
    request_otp,
    verify_contact_otp,
    verify_otp,
)
from apps.auth.sms import send_sms
from apps.auth.tasks import send_otp_email, send_otp_sms
from apps.users.models import User
from apps.wallet.models import Account, LedgerEntry
from apps.wallet.services import transfer

pytestmark = pytest.mark.django_db
AUTH = "/api/v1/auth/"


@pytest.fixture(autouse=True)
def email_transport():
    with override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEFAULT_FROM_EMAIL="synthetic@blasa.test"
    ):
        yield


def _client(user=None, token=None):
    client = APIClient()
    if token is not None:
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    elif user is not None:
        client.force_authenticate(user=user)
    return client


def _email_code(client, email):
    with TestCase.captureOnCommitCallbacks(execute=True):
        response = client.post(AUTH + "email/request/", {"email": email}, format="json")
    assert response.status_code == 200
    assert response.data == {"detail": "If eligible, a verification code will be sent."}
    return re.search(r"[0-9]{6}", mail.outbox[-1].body).group()


def _proof(channel, contact, user=None, session_key=""):
    return request_contact_otp(
        channel=channel, contact=contact, purpose="LINK" if user else "LOGIN", user=user, session_key=session_key
    )


def _verify_link(client, channel, contact, code):
    return client.post(AUTH + "contact/verify/", {"channel": channel, "contact": contact, "code": code}, format="json")


def test_email_login_normalization_empty_profile_verified_metadata_and_jwt():
    client = _client()
    code = _email_code(client, "  EmailLogin@Example.COM ")
    otp = ContactOtp.objects.get()
    assert otp.contact == "emaillogin@example.com" and otp.channel == "EMAIL"
    assert otp.purpose == "LOGIN" and otp.initiating_user_id is None
    assert otp.code_hash == _hash(code) and code not in otp.code_hash
    assert abs((otp.expires_at - otp.created_at - CODE_TTL).total_seconds()) < 1
    response = client.post(AUTH + "email/verify/", {"email": "EMAILLOGIN@example.com", "code": code}, format="json")
    assert response.status_code == 200 and set(response.data) == {"access", "refresh"}
    user = User.objects.get()
    assert user.phone_number is None and user.email == "emaillogin@example.com"
    assert user.email_verified_at is not None and user.phone_verified_at is None
    assert user.profile.full_name == ""
    me = _client(token=response.data["access"]).get("/api/v1/users/me/")
    assert me.status_code == 200 and me.data["email_verified_at"]
    assert not me.data["onboarding_complete"]
    assert client.post(AUTH + "email/verify/", {"email": user.email, "code": code}, format="json").status_code == 400


def test_phone_success_sets_only_phone_proof_and_resend_invalidates_older_codes():
    first, first_code = request_otp(phone_number="+21698003001")
    second, second_code = request_otp(phone_number="+21698003001")
    first.refresh_from_db()
    assert first.consumed_at is not None and second.consumed_at is None
    if first_code != second_code:
        with pytest.raises(ValueError):
            verify_otp(phone_number=first.phone_number, code=first_code)
    user = verify_otp(phone_number=second.phone_number, code=second_code)
    assert user.phone_verified_at is not None and user.email_verified_at is None
    assert user.profile.full_name == ""


@pytest.mark.parametrize("channel", ["EMAIL", "PHONE"])
def test_inactive_login_generic_no_tokens_no_reactivation(channel):
    contact = "inactive@example.com" if channel == "EMAIL" else "+21698003002"
    user = User.objects.create_user(**{"email" if channel == "EMAIL" else "phone_number": contact}, is_active=False)
    if channel == "EMAIL":
        otp, code = _proof(channel, contact)
        path, payload = "email/verify/", {"email": contact, "code": code}
    else:
        otp, code = request_otp(phone_number=contact)
        path, payload = "otp/verify/", {"phone_number": contact, "code": code}
    response = _client().post(AUTH + path, payload, format="json")
    assert response.status_code == 400 and response.data == {"detail": INVALID_CODE}
    user.refresh_from_db()
    otp.refresh_from_db()
    assert not user.is_active and user.phone_verified_at is None and user.email_verified_at is None
    assert otp.consumed_at is not None and User.objects.count() == 1


@pytest.mark.parametrize("purpose", ["LOGIN", "LINK"])
def test_email_limited_attempts_expiry_and_resend_are_db_backed(purpose):
    user = User.objects.create_user("+21698003003") if purpose == "LINK" else None
    otp, code = _proof("EMAIL", "proof@example.com", user)
    kwargs = dict(channel="EMAIL", contact=otp.contact, purpose=purpose, user=user)
    wrong = "000000" if code != "000000" else "000001"
    for attempt in range(MAX_ATTEMPTS):
        with pytest.raises(ValueError):
            verify_contact_otp(**kwargs, code=wrong)
        otp.refresh_from_db()
        assert otp.attempts == attempt + 1
    with pytest.raises(ValueError):
        verify_contact_otp(**kwargs, code=code)
    replacement, new_code = _proof("EMAIL", otp.contact, user)
    otp.refresh_from_db()
    assert otp.consumed_at is not None
    ContactOtp.objects.filter(pk=replacement.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(ValueError):
        verify_contact_otp(**kwargs, code=new_code)
    last, last_code = _proof("EMAIL", otp.contact, user)
    result = verify_contact_otp(**kwargs, code=last_code)
    assert result.email == otp.contact
    last.refresh_from_db()
    assert last.consumed_at is not None
    with pytest.raises(ValueError):
        verify_contact_otp(**kwargs, code=last_code)


def test_expired_sms_and_zero_time_boundary_are_rejected():
    otp, code = request_otp(phone_number="+21698003004")
    OtpCode.objects.filter(pk=otp.pk).update(expires_at=timezone.now())
    with pytest.raises(ValueError):
        verify_otp(phone_number=otp.phone_number, code=code)
    assert not User.objects.exists()


def test_email_request_responses_generic_for_new_existing_inactive_and_rate_limit():
    User.objects.create_user(email="known@example.com")
    User.objects.create_user(email="disabled@example.com", is_active=False)
    client = _client()
    responses = []
    with patch("apps.auth.views.send_otp_email") as task, TestCase.captureOnCommitCallbacks(execute=True):
        for email in ["new@example.com", "known@example.com", "disabled@example.com"]:
            responses.append(client.post(AUTH + "email/request/", {"email": email}, format="json"))
        for _ in range(MAX_CODES_PER_WINDOW):
            responses.append(client.post(AUTH + "email/request/", {"email": "known@example.com"}, format="json"))
    assert all(response.status_code == 200 and response.data == responses[0].data for response in responses)
    assert ContactOtp.objects.filter(contact="known@example.com").count() == MAX_CODES_PER_WINDOW
    assert task.delay.call_count == 5


@pytest.mark.parametrize("channel,contact", [("EMAIL", " Link@Example.com "), ("PHONE", "+21698003005")])
def test_authenticated_contact_request_and_link_contract(channel, contact):
    user = User.objects.create_user(email="original@example.com", preferred_language="ar")
    client = _client(user)
    task_name = "send_otp_email" if channel == "EMAIL" else "send_otp_sms"
    with patch("apps.auth.views." + task_name) as task, TestCase.captureOnCommitCallbacks(execute=True):
        request = client.post(AUTH + "contact/request/", {"channel": channel, "contact": contact}, format="json")
    assert request.status_code == 200 and task.delay.call_args.kwargs["language"] == "ar"
    code = task.delay.call_args.kwargs["code"]
    response = _verify_link(client, channel, contact, code)
    assert response.status_code == 200
    assert "access" not in response.data and "refresh" not in response.data
    user.refresh_from_db()
    if channel == "EMAIL":
        assert user.email == "link@example.com" and user.email_verified_at is not None
        assert user.phone_number is None
    else:
        assert user.phone_number == contact and user.phone_verified_at is not None
        assert user.email == "original@example.com"
    assert _verify_link(client, channel, contact, code).status_code == 400


@pytest.mark.parametrize("channel,contact", [("EMAIL", "owned@example.com"), ("PHONE", "+21698003006")])
def test_contact_conflict_preserves_both_accounts_and_real_wallet_ledgers(channel, contact):
    owner = User.objects.create_user(**{"email" if channel == "EMAIL" else "phone_number": contact})
    claimant = User.objects.create_user(email="claimant@example.com")
    funder = Account.objects.create(kind="SYSTEM", code="synthetic:funder", allow_negative=True)
    for user in [owner, claimant]:
        wallet = Account.objects.create(kind="USER", owner=user, code=f"user:{user.pk}")
        transfer(
            source=funder, destination=wallet, amount=Decimal("10"), kind="TOPUP", reference=f"synthetic:{user.pk}"
        )
    before_users = list(User.objects.order_by("id").values("id", "email", "phone_number"))
    before_wallets = list(Account.objects.order_by("id").values())
    before_entries = list(LedgerEntry.objects.order_by("id").values())
    otp, code = _proof(channel, contact, claimant)
    response = _verify_link(_client(claimant), channel, contact, code)
    assert response.status_code == 400 and response.data == {"detail": INVALID_CODE}
    assert list(User.objects.order_by("id").values("id", "email", "phone_number")) == before_users
    assert list(Account.objects.order_by("id").values()) == before_wallets
    assert list(LedgerEntry.objects.order_by("id").values()) == before_entries
    otp.refresh_from_db()
    assert otp.consumed_at is not None


def test_login_cannot_be_used_for_link_and_link_is_bound_to_initiating_user_and_session():
    user = User.objects.create_user("+21698003007")
    other = User.objects.create_user("+21698003008")
    _, login_code = _proof("EMAIL", "purpose@example.com")
    assert _verify_link(_client(user), "EMAIL", "purpose@example.com", login_code).status_code == 400
    access1 = RefreshToken.for_user(user).access_token
    access2 = RefreshToken.for_user(user).access_token
    otp, link_code = _proof("EMAIL", "purpose@example.com", user, str(access1["jti"]))
    assert _verify_link(_client(other), "EMAIL", otp.contact, link_code).status_code == 400
    assert _verify_link(_client(token=str(access2)), "EMAIL", otp.contact, link_code).status_code == 400
    assert (
        _client().post(AUTH + "email/verify/", {"email": otp.contact, "code": link_code}, format="json").status_code
        == 400
    )
    response = _verify_link(_client(token=str(access1)), "EMAIL", otp.contact, link_code)
    assert response.status_code == 200 and response.data["id"] == str(user.pk)
    other.refresh_from_db()
    assert other.email == ""


def test_contact_proof_survives_refresh_but_not_another_login_session():
    client = _client()
    code = _email_code(client, "session@example.com")
    tokens = client.post(AUTH + "email/verify/", {"email": "session@example.com", "code": code}, format="json").data
    contact = "linked-session@example.com"
    with patch("apps.auth.views.send_otp_email") as task, TestCase.captureOnCommitCallbacks(execute=True):
        result = _client(token=tokens["access"]).post(
            AUTH + "contact/request/", {"channel": "EMAIL", "contact": contact}, format="json"
        )
    assert result.status_code == 200
    link_code = task.delay.call_args.kwargs["code"]
    other_code = _email_code(client, "session@example.com")
    other_tokens = client.post(
        AUTH + "email/verify/", {"email": "session@example.com", "code": other_code}, format="json"
    ).data
    assert _verify_link(_client(token=other_tokens["access"]), "EMAIL", contact, link_code).status_code == 400
    rotated = client.post(AUTH + "token/refresh/", {"refresh": tokens["refresh"]}, format="json")
    assert rotated.status_code == 200
    assert _verify_link(_client(token=rotated.data["access"]), "EMAIL", contact, link_code).status_code == 200


def test_sms_login_proof_cannot_link_phone_and_link_proof_cannot_login():
    user = User.objects.create_user(email="phone-link@example.com")
    phone = "+21698003009"
    _, login_code = request_otp(phone_number=phone)
    assert _verify_link(_client(user), "PHONE", phone, login_code).status_code == 400
    _, link_code = _proof("PHONE", phone, user)
    # Distinct purpose storage remains independent even for the same recipient.
    if link_code != login_code:
        assert (
            _client().post(AUTH + "otp/verify/", {"phone_number": phone, "code": link_code}, format="json").status_code
            == 400
        )
    assert _verify_link(_client(user), "PHONE", phone, link_code).status_code == 200


def test_link_last_contact_replacement_cannot_clear_it_and_inactive_is_rejected():
    user = User.objects.create_user(email="last@example.com")
    client = _client(user)
    for contact in ["", " "]:
        assert _verify_link(client, "EMAIL", contact, "123456").status_code == 400
    _, code = _proof("EMAIL", "replacement@example.com", user)
    assert _verify_link(client, "EMAIL", "replacement@example.com", code).status_code == 200
    user.refresh_from_db()
    assert user.email == "replacement@example.com" and user.phone_number is None
    _, code = _proof("PHONE", "+21698003010", user)
    User.objects.filter(pk=user.pk).update(is_active=False)
    response = _verify_link(client, "PHONE", "+21698003010", code)
    assert response.status_code == 400 and response.data == {"detail": INVALID_CODE}
    user.refresh_from_db()
    assert not user.is_active and user.phone_number is None


@pytest.mark.parametrize(
    "path,payload",
    [
        ("email/request/", {"email": "invalid"}),
        ("email/verify/", {"email": "ok@example.com", "code": "١٢٣٤٥٦"}),
        ("otp/request/", {"phone_number": "98000001"}),
        ("otp/request/", {"phone_number": "+216١٢٣٤٥٦٧٨"}),
        ("otp/verify/", {"phone_number": "+21698000001", "code": "12345"}),
        ("contact/request/", {"channel": "EMAIL", "contact": "invalid"}),
        ("contact/request/", {"channel": "PHONE", "contact": "+216123"}),
        ("contact/request/", {"channel": "email", "contact": "ok@example.com"}),
    ],
)
def test_boundary_validation(path, payload):
    user = User.objects.create_user(email="boundary@example.com")
    assert _client(user).post(AUTH + path, payload, format="json").status_code == 400
    assert not OtpCode.objects.exists() and not ContactOtp.objects.exists()


def test_link_endpoints_require_authentication():
    client = _client()
    for path in ["contact/request/", "contact/verify/"]:
        assert client.post(AUTH + path, {}, format="json").status_code == 401


def test_dispatch_is_after_commit_and_discarded_on_rollback():
    with patch("apps.auth.views.send_otp_email") as task:
        with TestCase.captureOnCommitCallbacks(execute=True):
            with pytest.raises(RuntimeError), transaction.atomic():
                response = _client().post(AUTH + "email/request/", {"email": "rollback@example.com"}, format="json")
                assert response.status_code == 200
                assert ContactOtp.objects.exists()
                task.delay.assert_not_called()
                raise RuntimeError("synthetic rollback")
        task.delay.assert_not_called()
        assert not ContactOtp.objects.exists()
        with TestCase.captureOnCommitCallbacks(execute=True):
            _client().post(AUTH + "email/request/", {"email": "commit@example.com"}, format="json")
            task.delay.assert_not_called()
        task.delay.assert_called_once()


@pytest.mark.parametrize(
    "language,expected", [("en", "verification code"), ("fr", "code de vérification"), ("ar", "رمز التحقق")]
)
def test_real_locmem_email_localized_for_known_user(language, expected):
    user = User.objects.create_user(email="localized@example.com", preferred_language=language)
    code = _email_code(_client(), user.email)
    message = mail.outbox[-1]
    assert expected in message.body and code in message.body
    assert message.to == [user.email]


def test_transport_never_logs_otp_or_body_in_development(caplog, capsys):
    code = "845921"
    send_sms("+21698003011", "Synthetic sensitive body " + code)
    send_sms("forged\n" + code, "body")
    send_otp_sms(phone_number="+21698003011", code=code, language="ar")
    for backend in ["console", "filebased", "dummy", "smtp"]:
        with override_settings(EMAIL_BACKEND=f"django.core.mail.backends.{backend}.EmailBackend", EMAIL_HOST=""):
            send_otp_email(email="synthetic@example.com", code=code)
    output = caplog.text + capsys.readouterr().out
    assert code not in output and "Synthetic sensitive body" not in output
    assert "+21698003011" in output


def test_touched_auth_endpoints_have_spectacular_request_and_response_schemas():
    schema = SchemaGenerator().get_schema(request=None, public=True)
    for endpoint in [
        "otp/request",
        "otp/verify",
        "email/request",
        "email/verify",
        "contact/request",
        "contact/verify",
        "logout",
        "token/refresh",
    ]:
        operation = schema["paths"][AUTH + endpoint + "/"]["post"]
        assert operation["requestBody"]["content"]["application/json"]["schema"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]


def _parallel(*jobs):
    barrier = Barrier(len(jobs))

    def run(job):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                return job()
            except ValueError:
                return None
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(run, job) for job in jobs]
        return [future.result(timeout=20) for future in futures]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("channel", ["EMAIL", "PHONE"])
def test_concurrent_verification_consumes_once_and_creates_one_account(channel):
    contact = "concurrent@example.com" if channel == "EMAIL" else "+21698003012"
    if channel == "EMAIL":
        otp, code = _proof(channel, contact)
        job = lambda: verify_contact_otp(channel=channel, contact=contact, code=code, purpose="LOGIN")
    else:
        otp, code = request_otp(phone_number=contact)
        job = lambda: verify_otp(phone_number=contact, code=code)
    results = _parallel(job, job)
    assert sum(result is not None for result in results) == 1
    assert User.objects.count() == 1
    otp.refresh_from_db()
    assert otp.consumed_at is not None


@pytest.mark.django_db(transaction=True)
def test_concurrent_request_flood_guard_handles_initially_absent_rows():
    results = _parallel(*(lambda: _proof("EMAIL", "flood@example.com") for _ in range(6)))
    assert sum(result is not None for result in results) == MAX_CODES_PER_WINDOW
    assert ContactOtp.objects.filter(consumed_at__isnull=True).count() == 1
    assert ContactOtp.objects.count() == MAX_CODES_PER_WINDOW


@pytest.mark.django_db(transaction=True)
def test_concurrent_link_claimants_cannot_steal_or_merge():
    users = [User.objects.create_user(email=f"claim{n}@example.com") for n in range(2)]
    for user in users:
        Account.objects.create(kind="USER", owner=user, code=f"user:{user.pk}")
    proofs = [_proof("PHONE", "+21698003013", user) for user in users]
    jobs = [
        lambda user=user, code=code: verify_contact_otp(
            channel="PHONE",
            contact="+21698003013",
            code=code,
            purpose="LINK",
            user=user,
        )
        for user, (_, code) in zip(users, proofs, strict=True)
    ]
    results = _parallel(*jobs)
    assert sum(result is not None for result in results) == 1
    assert User.objects.count() == 2 and User.objects.filter(phone_number="+21698003013").count() == 1
    assert Account.objects.count() == 2
    assert set(Account.objects.values_list("owner_id", flat=True)) == {user.pk for user in users}


@pytest.mark.django_db(transaction=True)
def test_concurrent_wrong_codes_preserve_all_attempt_increments():
    otp, code = _proof("EMAIL", "attempts@example.com")
    wrong = "000000" if code != "000000" else "000001"
    jobs = [
        lambda: verify_contact_otp(channel="EMAIL", contact=otp.contact, code=wrong, purpose="LOGIN")
        for _ in range(MAX_ATTEMPTS)
    ]
    assert _parallel(*jobs) == [None] * MAX_ATTEMPTS
    otp.refresh_from_db()
    assert otp.attempts == MAX_ATTEMPTS and otp.consumed_at is None
