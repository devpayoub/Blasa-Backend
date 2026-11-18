from django.urls import include, path

urlpatterns = [
    path("users/", include("apps.users.urls")),
    path("auth/", include("apps.auth.urls")),
    path("verification/", include("apps.verification.urls")),
    path("vehicles/", include("apps.vehicles.urls")),
    path("rides/", include("apps.rides.urls")),
    path("bookings/", include("apps.bookings.urls")),
    path("conversations/", include("apps.messaging.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("ratings/", include("apps.ratings.urls")),
    path("interests/", include("apps.interests.urls")),
    path("matching/", include("apps.matching.urls")),
    path("wallet/", include("apps.wallet.urls")),
    path("payments/", include("apps.payments.urls")),
    path("brands/", include("apps.brands.urls")),
    path("incentives/", include("apps.incentives.urls")),
    path("moderation/", include("apps.moderation.urls")),
    path("reporting/", include("apps.reporting.urls")),
]
