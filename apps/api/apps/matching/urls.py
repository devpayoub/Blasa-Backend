from django.urls import path

from .views import InterestsForRideView, RidesForInterestView

urlpatterns = [
    path("interests/<uuid:interest_id>/rides/", RidesForInterestView.as_view(), name="rides-for-interest"),
    path("rides/<uuid:ride_id>/interests/", InterestsForRideView.as_view(), name="interests-for-ride"),
]
