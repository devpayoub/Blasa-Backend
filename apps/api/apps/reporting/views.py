import urllib.parse

from django.http import HttpResponse
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import selectors


class RidesSummaryView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(selectors.rides_summary())


class RevenueSummaryView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        try:
            days = int(request.query_params.get("days", 30))
        except ValueError:
            return Response({"detail": "days must be an integer"}, status=400)
        return Response(selectors.revenue_summary(days=days))


class CancellationSummaryView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(selectors.cancellation_summary())


class ExportView(APIView):
    """CSV export: /reporting/export/?dataset=rides|bookings|ledger"""

    permission_classes = [IsAdminUser]

    def get(self, request):
        dataset = request.query_params.get("dataset", "")
        try:
            filename, content = selectors.export_csv(dataset)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{urllib.parse.quote(filename)}"'
        return response
