from django.urls import path

from .views import EarningsView, LedgerHistoryView, WalletView

urlpatterns = [
    path("me/", WalletView.as_view(), name="wallet-me"),
    path("entries/", LedgerHistoryView.as_view(), name="wallet-entries"),
    path("earnings/", EarningsView.as_view(), name="wallet-earnings"),
]
