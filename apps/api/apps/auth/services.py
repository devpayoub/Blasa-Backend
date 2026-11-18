import hashlib
import hmac
import secrets
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.users.models import Profile, User

from .models import ContactOtp, OtpCode
from .serializers import ContactRequestSerializer, normalize_contact

CODE_TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 5
MAX_CODES_PER_WINDOW = 3
FLOOD_WINDOW = timedelta(minutes=15)
INVALID_CODE = "Code is invalid or expired"
REQUEST_LIMIT = "Too many codes requested. Try again later."


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _normalize(channel, contact):
    serializer = ContactRequestSerializer(data={"channel": channel, "contact": contact})
    try:
        serializer.is_valid(raise_exception=True)
    except ValidationError as exc:
        raise ValueError("Invalid contact") from exc
    return serializer.validated_data["contact"]


def contact_status(contact: str) -> dict:
    """Whether a single email/phone identifier already has an account.

    Deliberately returns no name or profile data: the client only needs to know
    whether to reveal the password field or start the registration steps.
    """
    channel, normalized = normalize_contact(contact)
    user = _owner(channel, normalized).only("pk", "password", "is_active").first()
    return {
        "channel": channel,
        "exists": user is not None,
        "has_password": bool(user is not None and user.is_active and user.has_usable_password()),
    }


def authenticate_with_password(contact: str, password: str):
    """Return the active account for a correct contact/password pair, else None.

    Callers must answer with the same generic error for unknown contacts and bad
    passwords so the endpoint is not an account-existence oracle.
    """
    channel, normalized = normalize_contact(contact)
    user = _owner(channel, normalized).first()
    if user is None or not user.is_active or not user.has_usable_password():
        # Run the hasher anyway so a missing account costs the same as a wrong password.
        User().set_password(password)
        return None
    return user if user.check_password(password) else None


def complete_registration(*, user, full_name: str, password: str, role: str) -> User:
    """Finish an OTP-created account: set the password, name and role once.

    A password can only be set while the account still has none, so this cannot
    be replayed to overwrite an existing credential. Role is limited to the
    choices accepted by the serializer; driver mode still needs an approved CIN
    before a vehicle becomes bookable or a ride can be published.
    """
    if role not in (User.Role.PASSENGER, User.Role.DRIVER):
        raise ValueError("Invalid role")
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        if locked.has_usable_password():
            raise ValueError("This account already has a password")
        locked.set_password(password)
        locked.role = role
        # Never trust a client-supplied verification claim.
        locked.is_verified = False
        locked.save(update_fields=["password", "role", "is_verified", "updated_at"])
        profile, _ = Profile.objects.get_or_create(user=locked)
        profile.full_name = full_name
        profile.save(update_fields=["full_name", "updated_at"])
    return locked


def _lock_contact(channel, contact):
    """PostgreSQL transaction lock also serializes requests for contacts with no rows yet.

    Shared by SMS login and phone linking. Database unique constraints remain the
    final guard against writers outside these services (e.g. admin/manager).
    Always acquire this before locking a User row.
    """
    key = int.from_bytes(hashlib.sha256(f"{channel}:{contact}".encode()).digest()[:8], byteorder="big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def _check_code(otp, code, now):
    if otp is None or otp.expires_at <= now or otp.attempts >= MAX_ATTEMPTS:
        return INVALID_CODE
    if not hmac.compare_digest(otp.code_hash, _hash(code)):
        otp.attempts += 1
        otp.save(update_fields=["attempts", "updated_at"])
        return INVALID_CODE
    otp.consumed_at = now
    otp.save(update_fields=["consumed_at", "updated_at"])
    return None


def _owner(channel, contact):
    return User.objects.filter(
        **{
            "email__iexact" if channel == ContactOtp.Channel.EMAIL else "phone_number": contact,
        }
    )


def _login_account(channel, contact, now):
    user = _owner(channel, contact).select_for_update().first()
    if user is None:
        try:
            # A savepoint protects OTP consumption/bookkeeping from uniqueness races.
            with transaction.atomic():
                user = User.objects.create_user(
                    **{
                        "email" if channel == ContactOtp.Channel.EMAIL else "phone_number": contact,
                    }
                )
        except IntegrityError:
            user = _owner(channel, contact).select_for_update().first()
            if user is None:
                raise ValueError(INVALID_CODE) from None
    if not user.is_active:
        raise ValueError(INVALID_CODE)
    field = "email_verified_at" if channel == ContactOtp.Channel.EMAIL else "phone_verified_at"
    setattr(user, field, now)
    user.save(update_fields=[field, "updated_at"])
    return user


def request_otp(*, phone_number: str) -> tuple[OtpCode, str]:
    phone_number = _normalize(ContactOtp.Channel.PHONE, phone_number)
    with transaction.atomic():
        _lock_contact(ContactOtp.Channel.PHONE, phone_number)
        now = timezone.now()
        recent = OtpCode.objects.filter(
            phone_number=phone_number,
            created_at__gte=now - FLOOD_WINDOW,
        ).count()
        # Include linking requests in the same recipient flood budget.
        recent += ContactOtp.objects.filter(
            channel=ContactOtp.Channel.PHONE,
            contact=phone_number,
            created_at__gte=now - FLOOD_WINDOW,
        ).count()
        if recent >= MAX_CODES_PER_WINDOW:
            raise ValueError(REQUEST_LIMIT)
        OtpCode.objects.filter(phone_number=phone_number, consumed_at__isnull=True).update(
            consumed_at=now,
        )
        code = f"{secrets.randbelow(1_000_000):06d}"
        otp = OtpCode.objects.create(
            phone_number=phone_number,
            code_hash=_hash(code),
            expires_at=now + CODE_TTL,
        )
    return otp, code


def verify_otp(*, phone_number: str, code: str):
    phone_number = _normalize(ContactOtp.Channel.PHONE, phone_number)
    user = None
    with transaction.atomic():
        _lock_contact(ContactOtp.Channel.PHONE, phone_number)
        otp = (
            OtpCode.objects.select_for_update()
            .filter(
                phone_number=phone_number,
                consumed_at__isnull=True,
            )
            .first()
        )
        now = timezone.now()
        error = _check_code(otp, code, now)
        if error is None:
            try:
                with transaction.atomic():
                    user = _login_account(ContactOtp.Channel.PHONE, phone_number, now)
            except (IntegrityError, ValueError):
                error = INVALID_CODE
    # Raise outside the transaction so failed attempts and consumed proofs persist.
    if error is not None:
        raise ValueError(error)
    return user


def request_contact_otp(*, channel, contact, purpose, user=None, session_key=""):
    contact = _normalize(channel, contact)
    if purpose not in ContactOtp.Purpose.values:
        raise ValueError("Invalid purpose")
    if purpose == ContactOtp.Purpose.LOGIN and (channel != ContactOtp.Channel.EMAIL or user is not None):
        raise ValueError("Invalid purpose")
    if purpose == ContactOtp.Purpose.LINK and user is None:
        raise ValueError("Authentication required")
    with transaction.atomic():
        _lock_contact(channel, contact)
        if user is not None:
            user = User.objects.select_for_update().get(pk=user.pk)
            if not user.is_active:
                raise ValueError(INVALID_CODE)
        now = timezone.now()
        recent = ContactOtp.objects.filter(
            channel=channel,
            contact=contact,
            created_at__gte=now - FLOOD_WINDOW,
        ).count()
        if channel == ContactOtp.Channel.PHONE:
            recent += OtpCode.objects.filter(
                phone_number=contact,
                created_at__gte=now - FLOOD_WINDOW,
            ).count()
        if recent >= MAX_CODES_PER_WINDOW:
            raise ValueError(REQUEST_LIMIT)
        # Resending invalidates all older proofs for this user/purpose, including
        # older access-token sessions, but never invalidates another user's link.
        ContactOtp.objects.filter(
            channel=channel,
            contact=contact,
            purpose=purpose,
            initiating_user=user,
            consumed_at__isnull=True,
        ).update(consumed_at=now)
        code = f"{secrets.randbelow(1_000_000):06d}"
        otp = ContactOtp.objects.create(
            channel=channel,
            contact=contact,
            purpose=purpose,
            initiating_user=user,
            session_hash=_hash(session_key) if user is not None else "",
            code_hash=_hash(code),
            expires_at=now + CODE_TTL,
        )
    return otp, code


def verify_contact_otp(*, channel, contact, code, purpose, user=None, session_key=""):
    contact = _normalize(channel, contact)
    if purpose == ContactOtp.Purpose.LOGIN:
        if channel != ContactOtp.Channel.EMAIL or user is not None:
            raise ValueError(INVALID_CODE)
    elif purpose != ContactOtp.Purpose.LINK or user is None:
        raise ValueError(INVALID_CODE)
    result = None
    with transaction.atomic():
        _lock_contact(channel, contact)
        otp = (
            ContactOtp.objects.select_for_update()
            .filter(
                channel=channel,
                contact=contact,
                purpose=purpose,
                initiating_user=user,
                session_hash=_hash(session_key) if user is not None else "",
                consumed_at__isnull=True,
            )
            .first()
        )
        now = timezone.now()
        error = _check_code(otp, code, now)
        if error is None:
            try:
                with transaction.atomic():
                    if purpose == ContactOtp.Purpose.LOGIN:
                        result = _login_account(channel, contact, now)
                    else:
                        result = User.objects.select_for_update().get(pk=user.pk)
                        if not result.is_active:
                            raise ValueError(INVALID_CODE)
                        if _owner(channel, contact).exclude(pk=result.pk).exists():
                            # Never merge identities or move any wallet/ledger records.
                            raise ValueError(INVALID_CODE)
                        field = "email" if channel == ContactOtp.Channel.EMAIL else "phone_number"
                        verified_field = (
                            "email_verified_at" if channel == ContactOtp.Channel.EMAIL else "phone_verified_at"
                        )
                        setattr(result, field, contact)
                        setattr(result, verified_field, now)
                        result.save(update_fields=[field, verified_field, "updated_at"])
            except (IntegrityError, ValueError, User.DoesNotExist):
                error = INVALID_CODE
    if error is not None:
        raise ValueError(error)
    return result
