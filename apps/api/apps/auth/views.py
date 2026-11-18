from functools import partial

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.users.models import User
from apps.users.serializers import UserSerializer

from .models import ContactOtp
from .serializers import (
    CompleteRegistrationSerializer,
    ContactCheckResultSerializer,
    ContactCheckSerializer,
    ContactRequestSerializer,
    ContactVerifySerializer,
    DetailSerializer,
    EmailRequestSerializer,
    EmailVerifySerializer,
    LogoutSerializer,
    OtpRequestSerializer,
    OtpVerifySerializer,
    PasswordLoginSerializer,
    TokenPairSerializer,
)
from .services import (
    INVALID_CODE,
    authenticate_with_password,
    complete_registration,
    contact_status,
    request_contact_otp,
    request_otp,
    verify_contact_otp,
    verify_otp,
)
from .tasks import send_otp_email, send_otp_sms

REQUEST_DETAIL = "If eligible, a verification code will be sent."
INVALID_CREDENTIALS = "That email/phone and password don't match."


def _queue(channel, contact, code, user=None):
    if user is None:
        user = User.objects.filter(
            **{
                "email__iexact" if channel == "EMAIL" else "phone_number": contact,
            }
        ).first()
    language = user.preferred_language if user is not None else "en"
    task = send_otp_email if channel == "EMAIL" else send_otp_sms
    kwargs = {"email" if channel == "EMAIL" else "phone_number": contact, "code": code, "language": language}
    transaction.on_commit(partial(task.delay, **kwargs), robust=True)


def _token_response(user):
    # Serialize issuance against deactivation, without wrapping the OTP failure
    # transaction (which would erase failed-attempt increments).
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        if not user.is_active:
            return Response({"detail": INVALID_CODE}, status=status.HTTP_400_BAD_REQUEST)
        refresh = RefreshToken.for_user(user)
        refresh["auth_session"] = str(refresh["jti"])
        return Response({"access": str(refresh.access_token), "refresh": str(refresh)})


def _session_key(request):
    # A refresh-family claim keeps contact proofs bound through access-token rotation.
    return str(request.auth.get("auth_session", request.auth.get("jti", ""))) if request.auth is not None else ""


class OtpRequestView(APIView):
    """Ask for an SMS login code; the response never includes the code."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(request=OtpRequestSerializer, responses={200: DetailSerializer, 429: DetailSerializer})
    def post(self, request):
        serializer = OtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone_number = serializer.validated_data["phone_number"]
        try:
            _otp, code = request_otp(phone_number=phone_number)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        _queue("PHONE", phone_number, code)
        return Response({"detail": "A login code was sent by SMS."})


class OtpVerifyView(APIView):
    """Consume an SMS login proof and issue tokens only to an active account."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(request=OtpVerifySerializer, responses={200: TokenPairSerializer, 400: DetailSerializer})
    def post(self, request):
        serializer = OtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = verify_otp(**serializer.validated_data)
        except ValueError:
            return Response({"detail": INVALID_CODE}, status=status.HTTP_400_BAD_REQUEST)
        return _token_response(user)


class EmailRequestView(APIView):
    """Request an email login proof without disclosing account existence."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(request=EmailRequestSerializer, responses={200: DetailSerializer})
    def post(self, request):
        serializer = EmailRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contact = serializer.validated_data["email"]
        try:
            _otp, code = request_contact_otp(channel="EMAIL", contact=contact, purpose="LOGIN")
        except ValueError:
            pass
        else:
            _queue("EMAIL", contact, code)
        return Response({"detail": REQUEST_DETAIL})


class EmailVerifyView(APIView):
    """Consume an email LOGIN proof, creating an email-only account if necessary."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(request=EmailVerifySerializer, responses={200: TokenPairSerializer, 400: DetailSerializer})
    def post(self, request):
        serializer = EmailVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = verify_contact_otp(
                channel="EMAIL",
                contact=serializer.validated_data["email"],
                code=serializer.validated_data["code"],
                purpose="LOGIN",
            )
        except ValueError:
            return Response({"detail": INVALID_CODE}, status=status.HTTP_400_BAD_REQUEST)
        return _token_response(user)


class ContactRequestView(APIView):
    """Request a contact LINK proof bound to this account and access-token session."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(request=ContactRequestSerializer, responses={200: DetailSerializer})
    def post(self, request):
        serializer = ContactRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            _otp, code = request_contact_otp(
                **serializer.validated_data,
                purpose="LINK",
                user=request.user,
                session_key=_session_key(request),
            )
        except ValueError:
            pass
        else:
            _queue(serializer.validated_data["channel"], serializer.validated_data["contact"], code, request.user)
        return Response({"detail": REQUEST_DETAIL})


class ContactVerifyView(APIView):
    """Prove and replace a linked contact; never merge accounts or steal contacts."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(request=ContactVerifySerializer, responses={200: UserSerializer, 400: DetailSerializer})
    def post(self, request):
        serializer = ContactVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = verify_contact_otp(
                **serializer.validated_data,
                purpose=ContactOtp.Purpose.LINK,
                user=request.user,
                session_key=_session_key(request),
            )
        except ValueError:
            return Response({"detail": INVALID_CODE}, status=status.HTTP_400_BAD_REQUEST)
        return Response(UserSerializer(user).data)


class ContactCheckView(APIView):
    """Tell the client whether to reveal the password field or start registration.

    Only existence and "has a usable password" are returned — never a name,
    profile or contact confirmation beyond the input the caller already has.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=ContactCheckSerializer, responses={200: ContactCheckResultSerializer})
    def post(self, request):
        serializer = ContactCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = contact_status(serializer.validated_data["contact"])
        return Response(ContactCheckResultSerializer(result).data)


class PasswordLoginView(APIView):
    """Password sign-in for accounts that completed registration."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=PasswordLoginSerializer, responses={200: TokenPairSerializer, 400: DetailSerializer})
    def post(self, request):
        serializer = PasswordLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate_with_password(
            serializer.validated_data["contact"],
            serializer.validated_data["password"],
        )
        if user is None:
            return Response({"detail": INVALID_CREDENTIALS}, status=status.HTTP_400_BAD_REQUEST)
        return _token_response(user)


class CompleteRegistrationView(APIView):
    """Set name, password and role on the account created by the OTP proof."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=CompleteRegistrationSerializer, responses={200: UserSerializer, 400: DetailSerializer})
    def post(self, request):
        serializer = CompleteRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = complete_registration(user=request.user, **serializer.validated_data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(UserSerializer(user).data)


class AuthTokenRefreshView(TokenRefreshView):
    @extend_schema(request=LogoutSerializer, responses={200: TokenPairSerializer, 401: DetailSerializer})
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class LogoutView(APIView):
    """Blacklist the supplied refresh token."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=LogoutSerializer, responses={200: DetailSerializer, 400: DetailSerializer})
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError:
            return Response({"detail": "Invalid or expired token"}, status=400)
        return Response({"detail": "Logged out"})
