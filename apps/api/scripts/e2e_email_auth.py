#!/usr/bin/env python
"""Live end-to-end check of email-only registration and login.

Unlike `pytest` (which runs on the test settings, an empty throwaway database and
a locmem mail backend) this script drives a RUNNING backend over HTTP, using
whatever the real `.env` points at: the real database, real JWT signing, real
Brevo delivery. Nothing is mocked.

There are two ways to obtain the login code, because the API only ever stores
its SHA-256 hash:

  --mode auto   (default) Seed a known code straight into the database, then run
                every step over HTTP. Fully automated and repeatable, sends no
                email. Requires Django (run it inside the backend container or
                venv) because it needs ORM access.

  --mode email  Ask the API to send a real Brevo email, then read the code from
                the inbox. Works with the standard library alone, so it can hit
                the deployed backend from any machine. Pass --code, or let it
                prompt you.

Examples:
    # Local API, fully automated, real database, no mail sent
    python scripts/e2e_email_auth.py

    # Local API but a genuinely delivered Brevo email
    python scripts/e2e_email_auth.py --mode email --email you+e2e@gmail.com

    # Deployed backend from the host, no Python deps required
    python apps/api/scripts/e2e_email_auth.py --mode email \
        --base-url https://blasa-backend.onrender.com --email you+e2e@gmail.com

Notes:
  * The `otp` and `login` throttle scopes allow 10 requests per minute, and the
    per-contact flood budget allows 3 codes per 15 minutes. Use a fresh address
    (a Gmail `+tag` works) if you run this repeatedly.
  * `--mode email` against a local server needs a Celery worker, or
    CELERY_TASK_ALWAYS_EAGER=1, or nothing is actually sent. The script warns.
  * `--mode auto` deletes the account it created unless you pass --keep.
  * Exit code is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PASSWORD = "Blasa-E2E-2026!x"
WRONG_CODE = "000000"


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
def _decode(raw: bytes):
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", "replace")


class Api:
    """Minimal stdlib HTTP client so this runs with or without Django installed."""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def call(self, method: str, path: str, body=None, token: str | None = None, prefix: str = "/api/v1"):
        url = f"{self.base}{prefix}{path}"
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=payload, method=method)
        request.add_header("Accept", "application/json")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, _decode(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.read())
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"\nCannot reach {self.base} ({exc.reason}).\n"
                "Start the API first (make up), or point --base-url at a running host."
            ) from exc


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
class Reporter:
    def __init__(self):
        self.passed = 0
        self.failures: list[str] = []

    def check(self, label: str, ok: bool, detail="") -> bool:
        if ok:
            self.passed += 1
            print(f"  \033[32mPASS\033[0m  {label}")
        else:
            self.failures.append(label)
            print(f"  \033[31mFAIL\033[0m  {label}")
            if detail:
                print(f"        {detail}")
        return ok

    def section(self, title: str):
        print(f"\n\033[1m{title}\033[0m")


def _expect(reporter: Reporter, label: str, response: tuple[int, object], status: int) -> bool:
    code, body = response
    return reporter.check(label, code == status, f"expected HTTP {status}, got {code}: {body}")


# --------------------------------------------------------------------------- #
# database helpers (only used by --mode auto)
# --------------------------------------------------------------------------- #
def bootstrap_django() -> bool:
    if str(APP_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_ROOT))
    try:
        import django
    except ModuleNotFoundError:
        return False
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    django.setup()
    return True


def seed_login_code(contact: str, code: str) -> None:
    """Write the row `request_contact_otp` would have written, but with a known code."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.auth.models import ContactOtp

    now = timezone.now()
    ContactOtp.objects.filter(
        channel=ContactOtp.Channel.EMAIL, contact=contact, purpose=ContactOtp.Purpose.LOGIN, consumed_at__isnull=True
    ).update(consumed_at=now)
    ContactOtp.objects.create(
        channel=ContactOtp.Channel.EMAIL,
        contact=contact,
        purpose=ContactOtp.Purpose.LOGIN,
        initiating_user=None,
        session_hash="",
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        expires_at=now + timedelta(minutes=5),
    )


def describe_environment() -> None:
    """Show which real services this run is about to touch. Never prints secrets."""
    from django.conf import settings

    db = settings.DATABASES["default"]
    host = db.get("HOST") or "(local socket)"
    backend = settings.EMAIL_BACKEND.rsplit(".", 1)[-1]
    print("\033[1mEnvironment\033[0m")
    print(f"  database   {db.get('NAME')} @ {host}:{db.get('PORT') or 5432}")
    print(f"  email      {backend}")
    print(f"  settings   {os.environ.get('DJANGO_SETTINGS_MODULE')}")
    if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False) and backend == "BrevoEmailBackend":
        print("  \033[33mwarning\033[0m    Celery is not eager: a worker must be running or no email is sent")


def cleanup(email: str) -> None:
    from django.db import transaction

    from apps.auth.models import ContactOtp
    from apps.users.models import User

    try:
        with transaction.atomic():
            ContactOtp.objects.filter(channel=ContactOtp.Channel.EMAIL, contact=email).delete()
            deleted, _ = User.objects.filter(email__iexact=email).delete()
        print(f"\n  cleaned up {deleted} row(s) for {email}")
    except Exception as exc:  # noqa: BLE001 - cleanup must never mask the test result
        print(f"\n  \033[33mcould not clean up {email}: {exc}\033[0m (delete it from /admin/ if needed)")


def db_snapshot(email: str) -> dict:
    from apps.users.models import User

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return {}
    profile = getattr(user, "profile", None)
    return {
        "id": str(user.pk),
        "phone_number": user.phone_number,
        "email": user.email,
        "email_verified_at": user.email_verified_at,
        "has_usable_password": user.has_usable_password(),
        "role": user.role,
        "is_verified": user.is_verified,
        "full_name": getattr(profile, "full_name", ""),
        "accounts": User.objects.filter(email__iexact=email).count(),
    }


# --------------------------------------------------------------------------- #
# the flow
# --------------------------------------------------------------------------- #
def obtain_code(api: Api, reporter: Reporter, args, email: str) -> str:
    if args.mode == "auto":
        seed_login_code(email, args.code)
        reporter.check(f"seeded a known LOGIN code for {email}", True)
        return args.code

    reporter.section("2a. Request a real email code")
    _expect(reporter, "POST /auth/email/request/ -> 200", api.call("POST", "/auth/email/request/", {"email": email}), 200)
    if args.code:
        print("  using the code passed with --code")
        return args.code
    print(f"\n  A Brevo email was sent to {email}.")
    return input("  Enter the 6-digit code: ").strip()


def run(args, email: str, django_ready: bool) -> int:
    reporter = Reporter()
    api = Api(args.base_url)
    password = args.password

    print("\033[1mBlasa — email-only auth, end to end\033[0m")
    print(f"  base url   {api.base}")
    print(f"  account    {email}")
    print(f"  mode       {args.mode}")
    if django_ready:
        describe_environment()

    reporter.section("0. Preflight")
    status, body = api.call("GET", "/healthz", prefix="")
    reporter.check("GET /healthz -> 200", status == 200 and body == {"status": "ok"}, f"got {status}: {body}")

    reporter.section("1. A brand-new email looks unregistered")
    status, body = api.call("POST", "/auth/contact/check/", {"contact": email})
    reporter.check("POST /auth/contact/check/ -> 200", status == 200, f"got {status}: {body}")
    body = body or {}
    reporter.check("channel is EMAIL", body.get("channel") == "EMAIL", body)
    reporter.check("exists is false", body.get("exists") is False, body)
    reporter.check("has_password is false", body.get("has_password") is False, body)

    reporter.section("2. Prove the email and get tokens")
    code = obtain_code(api, reporter, args, email)

    wrong = WRONG_CODE if code != WRONG_CODE else "111111"
    _expect(
        reporter,
        "POST /auth/email/verify/ with a wrong code -> 400",
        api.call("POST", "/auth/email/verify/", {"email": email, "code": wrong}),
        400,
    )
    status, tokens = api.call("POST", "/auth/email/verify/", {"email": email, "code": code})
    reporter.check("POST /auth/email/verify/ -> 200", status == 200, f"got {status}: {tokens}")
    tokens = tokens or {}
    reporter.check("access token issued", bool(tokens.get("access")))
    reporter.check("refresh token issued", bool(tokens.get("refresh")))
    access = tokens.get("access")

    first_user_id = None
    if django_ready:
        snapshot = db_snapshot(email)
        first_user_id = snapshot.get("id")
        reporter.check("account exists exactly once", snapshot.get("accounts") == 1, snapshot)
        reporter.check("phone_number is NULL (email-only account)", snapshot.get("phone_number") is None, snapshot)
        reporter.check("email_verified_at is set", snapshot.get("email_verified_at") is not None, snapshot)
        reporter.check("no password yet", snapshot.get("has_usable_password") is False, snapshot)

    reporter.section("3. Finish registration: name, password, role")
    _expect(
        reporter,
        "POST /auth/register/complete/ -> 200",
        api.call(
            "POST",
            "/auth/register/complete/",
            {"full_name": "Blasa E2E Runner", "password": password, "role": "PASSENGER"},
            token=access,
        ),
        200,
    )
    _expect(
        reporter,
        "replaying registration is rejected -> 400",
        api.call(
            "POST",
            "/auth/register/complete/",
            {"full_name": "Blasa E2E Runner", "password": password, "role": "PASSENGER"},
            token=access,
        ),
        400,
    )

    if django_ready:
        snapshot = db_snapshot(email)
        reporter.check("password is stored (hashed)", snapshot.get("has_usable_password") is True, snapshot)
        reporter.check("role is PASSENGER", snapshot.get("role") == "PASSENGER", snapshot)
        reporter.check("full_name persisted on the profile", snapshot.get("full_name") == "Blasa E2E Runner", snapshot)
        reporter.check("still unverified (no CIN reviewed)", snapshot.get("is_verified") is False, snapshot)

    reporter.section("4. The same email is now a known account")
    status, body = api.call("POST", "/auth/contact/check/", {"contact": email})
    body = body or {}
    reporter.check("exists is true", status == 200 and body.get("exists") is True, body)
    reporter.check("has_password is true", body.get("has_password") is True, body)

    reporter.section("5. Password login with the email")
    _expect(
        reporter,
        "wrong password -> 400 with a generic message",
        api.call("POST", "/auth/password/login/", {"contact": email, "password": "definitely-not-it"}),
        400,
    )
    status, login_tokens = api.call("POST", "/auth/password/login/", {"contact": email.upper(), "password": password})
    reporter.check("POST /auth/password/login/ (uppercase email) -> 200", status == 200, f"got {status}: {login_tokens}")
    login_tokens = login_tokens or {}
    login_access, login_refresh = login_tokens.get("access"), login_tokens.get("refresh")
    reporter.check("login issued an access token", bool(login_access))

    reporter.section("6. The login token actually works")
    status, me = api.call("GET", "/users/me/", token=login_access)
    me = me or {}
    reporter.check("GET /users/me/ -> 200", status == 200, f"got {status}: {me}")
    reporter.check("me.email matches", str(me.get("email", "")).lower() == email, me)
    reporter.check("onboarding_complete is true", me.get("onboarding_complete") is True, me)

    reporter.section("7. Refresh, then logout")
    status, rotated = api.call("POST", "/auth/token/refresh/", {"refresh": login_refresh})
    rotated = rotated or {}
    reporter.check("POST /auth/token/refresh/ -> 200", status == 200, f"got {status}: {rotated}")
    reporter.check("rotation returned a new access token", bool(rotated.get("access")))
    new_refresh = rotated.get("refresh") or login_refresh
    _expect(
        reporter,
        "POST /auth/logout/ -> 200",
        api.call("POST", "/auth/logout/", {"refresh": new_refresh}, token=login_access),
        200,
    )
    status, _ = api.call("POST", "/auth/token/refresh/", {"refresh": new_refresh})
    reporter.check("a logged-out refresh token is dead -> 401", status == 401, f"got {status}")

    if django_ready:
        reporter.section("8. A second email login reuses the same account")
        seed_login_code(email, args.code)
        status, again = api.call("POST", "/auth/email/verify/", {"email": email, "code": args.code})
        reporter.check("POST /auth/email/verify/ again -> 200", status == 200, f"got {status}: {again}")
        snapshot = db_snapshot(email)
        reporter.check("still exactly one account for this email", snapshot.get("accounts") == 1, snapshot)
        reporter.check("same user id as the first login", snapshot.get("id") == first_user_id, snapshot)

    print()
    if reporter.failures:
        print(f"\033[31m{len(reporter.failures)} check(s) failed\033[0m, {reporter.passed} passed")
        for failure in reporter.failures:
            print(f"  - {failure}")
        return 1
    print(f"\033[32mAll {reporter.passed} checks passed\033[0m")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Live end-to-end check of email-only registration and login.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API root, no /api/v1 suffix")
    parser.add_argument(
        "--mode", choices=["auto", "email"], default="auto",
        help="auto: seed a known code in the DB; email: send and read a real one",
    )
    parser.add_argument("--email", help="address under test (default: a random @example.com in auto mode)")
    parser.add_argument("--code", default="246810", help="code for auto mode, or to skip the prompt in email mode")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="password set during registration")
    parser.add_argument("--keep", action="store_true", help="keep the created account (auto mode deletes it by default)")
    parser.add_argument("--cleanup", action="store_true", help="also delete the account in --mode email")
    args = parser.parse_args()

    django_ready = bootstrap_django()

    if args.mode == "auto" and not django_ready:
        parser.error(
            "--mode auto needs Django (it seeds the OTP row). Run it inside the backend container or venv, "
            "or use --mode email, which needs nothing but the standard library."
        )
    if args.mode == "email" and not args.email:
        parser.error("--mode email needs --email, because you have to read the code from that inbox.")
    if args.mode == "email" and args.email.lower().endswith("@example.com"):
        parser.error("--mode email sends a real message; use an inbox you can open (a Gmail +tag works).")

    email = (args.email or f"blasa.e2e.{uuid4().hex[:10]}@example.com").strip().lower()
    should_clean = django_ready and not args.keep and (args.mode == "auto" or args.cleanup)

    try:
        return run(args, email, django_ready)
    finally:
        if should_clean:
            cleanup(email)
        elif args.mode == "email":
            print(f"\n  account left in place: {email} (add --cleanup to remove it)")


if __name__ == "__main__":
    sys.exit(main())
