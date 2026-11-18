from django.apps import AppConfig


class AuthConfig(AppConfig):
    name = "apps.auth"
    label = "blasa_auth"  # "auth" is taken by django.contrib.auth
    default_auto_field = "django.db.models.BigAutoField"
