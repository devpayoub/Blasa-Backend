"""Diagnose the Brevo configuration without going through the login flow.

Answers the three questions that actually break OTP delivery:

1. Is BREVO_API_KEY set, and is it an *API* key rather than an SMTP key?
2. Does Brevo accept the key (GET /v3/account)?
3. Is DEFAULT_FROM_EMAIL one of the account's verified senders?

Usage:
    python manage.py brevo_check
    python manage.py brevo_check --to you@example.com
    python manage.py brevo_check --sms +21699000123
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from email.utils import parseaddr

from django.conf import settings
from django.core.management.base import BaseCommand

from core.delivery.brevo import send_transactional_sms

API_ROOT = "https://api.brevo.com/v3"
SENDERS_URL = f"{API_ROOT}/senders"
ACCOUNT_URL = f"{API_ROOT}/account"
TIMEOUT = 15


def _get(url: str, api_key: str):
    """Return (status, parsed-json-or-None, error-string-or-None)."""
    request = urllib.request.Request(
        url,
        headers={"api-key": api_key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read() or b"{}"), None
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
            detail = f"{body.get('code', '')}: {body.get('message', '')}".strip(": ")
        except Exception:  # noqa: BLE001
            detail = ""
        return exc.code, None, detail or f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, None, type(exc).__name__


class Command(BaseCommand):
    help = "Check that the Brevo API key and sender address are usable."

    def add_arguments(self, parser):
        parser.add_argument("--to", metavar="EMAIL", help="Send a test email to this address.")
        parser.add_argument("--sms", metavar="PHONE", help="Send a test SMS to this E.164 number.")

    def handle(self, *args, **options):
        key = getattr(settings, "BREVO_API_KEY", "") or ""
        sender = parseaddr(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "")[1]

        self.stdout.write(self.style.MIGRATE_HEADING("Brevo configuration"))
        self.stdout.write(f"  SMS_PROVIDER       {getattr(settings, 'SMS_PROVIDER', '?')}")
        self.stdout.write(f"  DEFAULT_FROM_EMAIL {sender or '(empty)'}")
        self.stdout.write(f"  BREVO_SMS_SENDER   {getattr(settings, 'BREVO_SMS_SENDER', '?')}")

        if not key:
            self.stdout.write("  BREVO_API_KEY      (empty)")
            self.stdout.write("")
            self._fail(
                "No API key. Email falls back to SMTP and SMS falls back to the "
                "console stub, so OTPs are never delivered."
            )
            self._explain()
            return

        # The single most common mistake: pasting the SMTP relay password here.
        masked = f"{key[:8]}…{key[-4:]}"
        self.stdout.write(f"  BREVO_API_KEY      {masked} ({len(key)} chars)")
        if key.startswith("xsmtpsib-"):
            self.stdout.write("")
            self._fail(
                "This is an SMTP key (xsmtpsib-…). The HTTP API rejects it with 401."
            )
            self._explain()
            return
        if not key.startswith("xkeysib-"):
            self.stdout.write(self.style.WARNING(
                "  ! API keys normally start with xkeysib-. Continuing anyway."
            ))

        status, account, error = _get(ACCOUNT_URL, key)
        if error:
            self.stdout.write("")
            self._fail(f"Brevo rejected the key: {error}")
            self._explain()
            return
        self.stdout.write(self.style.SUCCESS(f"  key accepted       HTTP {status}"))
        if isinstance(account, dict):
            self.stdout.write(f"  account            {account.get('email', '?')}")
            plan = (account.get("plan") or [{}])
            plan_name = plan[0].get("type", "?") if isinstance(plan, list) and plan else "?"
            self.stdout.write(f"  plan               {plan_name}")

        status, senders, error = _get(SENDERS_URL, key)
        verified: list[str] = []
        if error:
            self.stdout.write(self.style.WARNING(f"  ! could not list senders: {error}"))
        else:
            for entry in (senders or {}).get("senders", []) if isinstance(senders, dict) else []:
                email = str(entry.get("email", ""))
                active = entry.get("active", False)
                verified.append(email)
                mark = "verified" if active else "PENDING"
                self.stdout.write(f"  sender             {email}  [{mark}]")

        self.stdout.write("")
        if not sender:
            self._fail("DEFAULT_FROM_EMAIL is empty; Brevo needs a verified sender.")
        elif sender.lower() in {s.lower() for s in verified}:
            self.stdout.write(self.style.SUCCESS(
                f"  OK: {sender} is a verified sender on this account."
            ))
        else:
            self._fail(
                f"{sender} is not in the verified sender list. Brevo will answer "
                "HTTP 400 'sender not valid' and every OTP email is dropped."
            )
            self.stdout.write(
                "  Fix: Brevo -> Senders, Domains & IPs -> Senders -> add this exact\n"
                "  address and click the confirmation link Brevo emails you. A domain\n"
                "  is not required to verify a single address."
            )

        if options.get("to"):
            self._send_test_email(options["to"])
        if options.get("sms"):
            self._send_test_sms(options["sms"])

    # -- helpers ------------------------------------------------------------

    def _fail(self, message: str) -> None:
        self.stdout.write(self.style.ERROR(f"  FAIL  {message}"))

    def _explain(self) -> None:
        self.stdout.write("")
        self.stdout.write(
            "  The app talks to Brevo over HTTPS (api.brevo.com/v3), not SMTP:\n"
            "  Render's free tier blocks outbound SMTP ports, and SMS only exists\n"
            "  on the HTTP API. Use an API key, not the SMTP relay password."
        )

    def _send_test_email(self, recipient: str) -> None:
        from django.core.mail import send_mail

        self.stdout.write("")
        self.stdout.write(f"Sending a test email to {recipient} …")
        try:
            sent = send_mail(
                subject="Blasa — Brevo test",
                message="If you can read this, transactional email is wired correctly.",
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[recipient],
                fail_silently=False,
            )
        except Exception as exc:  # noqa: BLE001 - surface the provider's reason
            self._fail(f"{type(exc).__name__}: {exc}")
            return
        if sent:
            self.stdout.write(self.style.SUCCESS("  accepted by Brevo. Check the inbox (and spam)."))
        else:
            self._fail("Brevo did not accept the message. See the warning logged above.")

    def _send_test_sms(self, phone_number: str) -> None:
        self.stdout.write("")
        self.stdout.write(f"Sending a test SMS to {phone_number} …")
        if send_transactional_sms(phone_number, "Blasa test: SMS delivery works."):
            self.stdout.write(self.style.SUCCESS("  accepted by Brevo."))
        else:
            self._fail(
                "Not accepted. Check that BREVO_SMS_SENDER is an approved sender "
                "name and that the number is E.164 (+216…)."
            )
