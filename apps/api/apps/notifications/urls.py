from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import DeviceRegistryView, NotificationPreferencesView, NotificationReadView, NotificationViewSet

router = DefaultRouter()
router.register("", NotificationViewSet, basename="notification")

urlpatterns = [
    # Explicit paths first: the router's detail pattern would otherwise swallow
    # "devices/" as a notification pk and answer with 405.
    path("devices/", DeviceRegistryView.as_view(), name="device-tokens"),
    path("preferences/", NotificationPreferencesView.as_view(), name="notification-preferences"),
    path("<uuid:notification_id>/read/", NotificationReadView.as_view(), name="notification-read"),
    *router.urls,
]
