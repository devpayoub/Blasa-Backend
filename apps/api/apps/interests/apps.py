from django.apps import AppConfig


class InterestsConfig(AppConfig):
    name = "apps.interests"
    label = "blasa_interests"  # "interests" is fine, but keep the blasa_ prefix for uniformity
    default_auto_field = "django.db.models.BigAutoField"
