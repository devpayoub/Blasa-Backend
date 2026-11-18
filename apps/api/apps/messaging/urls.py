from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ConversationViewSet, MessageHistoryView

router = DefaultRouter()
router.register("", ConversationViewSet, basename="conversation")

urlpatterns = [
    *router.urls,
    path("<uuid:conversation_id>/messages/", MessageHistoryView.as_view(), name="message-history"),
]
