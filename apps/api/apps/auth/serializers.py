from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.users.models import User, UserManager

PHONE_PATTERN = r"^\+216[0-9]{8}$"
CODE_PATTERN = r"^[0-9]{6}$"


def normalize_contact(value: str) -> tuple[str, str]:
    """Return (channel, normalized contact) for a raw email/phone string.

    The single login field accepts either identifier, so the channel is derived
    from the value instead of a separate toggle in the client.
    """
    raw = (value or "").strip()
    if "@" in raw:
        return "EMAIL", NormalizedEmailField(max_length=254).run_validation(raw)
    return "PHONE", serializers.RegexField(PHONE_PATTERN, max_length=20).run_validation(raw)


class NormalizedEmailField(serializers.EmailField):
    def to_internal_value(self, data):
        return UserManager.normalize_email(super().to_internal_value(data))


class OtpRequestSerializer(serializers.Serializer):
    phone_number = serializers.RegexField(PHONE_PATTERN, max_length=20)


class OtpVerifySerializer(OtpRequestSerializer):
    code = serializers.RegexField(CODE_PATTERN)


class EmailRequestSerializer(serializers.Serializer):
    email = NormalizedEmailField(max_length=254)


class EmailVerifySerializer(EmailRequestSerializer):
    code = serializers.RegexField(CODE_PATTERN)


class ContactRequestSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=["EMAIL", "PHONE"])
    contact = serializers.CharField(max_length=254)

    def validate(self, attrs):
        field = (
            NormalizedEmailField(max_length=254)
            if attrs["channel"] == "EMAIL"
            else serializers.RegexField(PHONE_PATTERN, max_length=20)
        )
        attrs["contact"] = field.run_validation(attrs["contact"])
        return attrs


class ContactVerifySerializer(ContactRequestSerializer):
    code = serializers.RegexField(CODE_PATTERN)


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField(read_only=True)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class ContactCheckSerializer(serializers.Serializer):
    """One field for either an email address or a Tunisian phone number."""

    contact = serializers.CharField(max_length=254)

    def validate_contact(self, value):
        try:
            channel, contact = normalize_contact(value)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError(
                "Enter a valid email address or a Tunisian number like +216 20 000 000."
            ) from exc
        self._channel = channel
        return contact

    def validate(self, attrs):
        attrs["channel"] = getattr(self, "_channel", "PHONE")
        return attrs


class ContactCheckResultSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=["EMAIL", "PHONE"])
    exists = serializers.BooleanField()
    has_password = serializers.BooleanField()


class PasswordLoginSerializer(ContactCheckSerializer):
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class CompleteRegistrationSerializer(serializers.Serializer):
    """Name, password and role collected after the contact proof succeeds."""

    full_name = serializers.CharField(min_length=2, max_length=150)
    password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)
    role = serializers.ChoiceField(choices=[User.Role.PASSENGER, User.Role.DRIVER])

    def validate_full_name(self, value):
        name = " ".join(value.split())
        if len(name) < 2:
            raise serializers.ValidationError("Enter your full name.")
        return name

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value
