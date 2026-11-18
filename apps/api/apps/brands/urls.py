from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import BrandViewSet, CampaignEventView, CampaignPublicView, CampaignViewSet, SponsoredRideViewSet

router = DefaultRouter()
router.register("brands", BrandViewSet, basename="brand")
router.register("campaigns", CampaignViewSet, basename="campaign")
router.register("sponsored-rides", SponsoredRideViewSet, basename="sponsored-ride")

urlpatterns = [
    # Literal prefixes first: the router's campaign detail pattern would otherwise
    # swallow "sponsorship/" as a campaign pk.
    path("sponsorship/<uuid:ride_id>/", CampaignPublicView.as_view(), name="campaign-public"),
    path("sponsorship/<uuid:ride_id>/events/", CampaignEventView.as_view(), name="campaign-event"),
    *router.urls,
]
