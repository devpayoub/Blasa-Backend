from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import FuelRedeemView, IncentiveCampaignViewSet, RewardListView, VoucherViewSet

router = DefaultRouter()
router.register("vouchers", VoucherViewSet, basename="voucher")
router.register("campaigns", IncentiveCampaignViewSet, basename="incentive-campaign")

urlpatterns = [
    path("rewards/", RewardListView.as_view(), name="incentive-rewards"),
    path("fuel/redeem/", FuelRedeemView.as_view(), name="fuel-redeem"),
    *router.urls,
]
