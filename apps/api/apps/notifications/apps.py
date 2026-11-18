from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "apps.notifications"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from core.events import subscribe

        from . import handlers

        for event, handler in {
            "booking.created": handlers.on_booking_created,
            "booking.status_changed": handlers.on_booking_status_changed,
            "ride.published": handlers.on_ride_published,
            "ride.status_changed": handlers.on_ride_status_changed,
            "match.found": handlers.on_match_found,
            "verification.changed": handlers.on_verification_changed,
            "payment.status_changed": handlers.on_payment_status_changed,
            "wallet.posted": handlers.on_wallet_posted,
            "incentive.awarded": handlers.on_incentive_awarded,
        }.items():
            subscribe(event, handler, transactional=True)
