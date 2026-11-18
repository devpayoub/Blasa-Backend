from django.urls import path

from .views import (
    PaymentIntentCancelView,
    PaymentIntentDetailView,
    PaymentIntentListView,
    TopupView,
    WebhookView,
)

urlpatterns = [
    path("topup/", TopupView.as_view(), name="topup"),
    path("intents/", PaymentIntentListView.as_view(), name="payment-intents"),
    path("intents/<uuid:pk>/", PaymentIntentDetailView.as_view(), name="payment-intent-detail"),
    path("intents/<uuid:pk>/cancel/", PaymentIntentCancelView.as_view(), name="payment-intent-cancel"),
    path("webhooks/<str:gateway>/", WebhookView.as_view(), name="payment-webhook"),
]
