from django.urls import path

from .views import (
    AuthTokenRefreshView,
    CompleteRegistrationView,
    ContactCheckView,
    ContactRequestView,
    ContactVerifyView,
    EmailRequestView,
    EmailVerifyView,
    LogoutView,
    OtpRequestView,
    OtpVerifyView,
    PasswordLoginView,
)

urlpatterns = [
    path("contact/check/", ContactCheckView.as_view(), name="contact-check"),
    path("password/login/", PasswordLoginView.as_view(), name="password-login"),
    path("register/complete/", CompleteRegistrationView.as_view(), name="register-complete"),
    path("otp/request/", OtpRequestView.as_view(), name="otp-request"),
    path("otp/verify/", OtpVerifyView.as_view(), name="otp-verify"),
    path("email/request/", EmailRequestView.as_view(), name="email-request"),
    path("email/verify/", EmailVerifyView.as_view(), name="email-verify"),
    path("contact/request/", ContactRequestView.as_view(), name="contact-request"),
    path("contact/verify/", ContactVerifyView.as_view(), name="contact-verify"),
    path("token/refresh/", AuthTokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
]
