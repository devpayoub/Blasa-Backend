from django.urls import path

from .views import (
    AvatarConfirmView,
    AvatarDownloadView,
    AvatarUploadView,
    BecomeDriverView,
    InterestTagListView,
    MeView,
    UserPublicProfileView,
)

urlpatterns = [
    path("interest-tags/", InterestTagListView.as_view(), name="interest-tags"),
    path("me/", MeView.as_view(), name="me"),
    path("me/become-driver/", BecomeDriverView.as_view(), name="become-driver"),
    path("me/avatar/upload-url/", AvatarUploadView.as_view(), name="avatar-upload"),
    path("me/avatar/confirm-upload/", AvatarConfirmView.as_view(), name="avatar-confirm"),
    path("me/avatar/download-url/", AvatarDownloadView.as_view(), name="avatar-download"),
    # Explicit paths above first: "<uuid:user_id>/" would otherwise swallow nothing here,
    # but keeping the literal routes first documents the ordering intent.
    path("<uuid:user_id>/", UserPublicProfileView.as_view(), name="user-public-profile"),
]
