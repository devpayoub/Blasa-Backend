from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AuditTrailView, BanListView, BanView, ReportReviewView, ReportViewSet, RideOverrideView

router = DefaultRouter()
router.register("reports", ReportViewSet, basename="report")

urlpatterns = [
    path("users/<uuid:user_id>/ban/", BanView.as_view(), name="ban"),
    path("bans/", BanListView.as_view(), name="ban-list"),
    path("reports/<uuid:report_id>/review/", ReportReviewView.as_view(), name="report-review"),
    path("rides/<uuid:ride_id>/override/", RideOverrideView.as_view(), name="ride-override"),
    path("audit/<str:object_type>/<str:object_id>/", AuditTrailView.as_view(), name="audit-trail"),
    *router.urls,
]
