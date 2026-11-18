from django.urls import path

from .views import CancellationSummaryView, ExportView, RevenueSummaryView, RidesSummaryView

urlpatterns = [
    path("rides/", RidesSummaryView.as_view(), name="reporting-rides"),
    path("revenue/", RevenueSummaryView.as_view(), name="reporting-revenue"),
    path("cancellations/", CancellationSummaryView.as_view(), name="reporting-cancellations"),
    path("export/", ExportView.as_view(), name="reporting-export"),
]
