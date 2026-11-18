from django.apps import AppConfig


class UsersConfig(AppConfig):
    name = "apps.users"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from core.events import subscribe

        from .services import handle_verification_changed

        for event in ("verification.changed", "verification.approved", "verification.rejected"):
            subscribe(event, handle_verification_changed)
