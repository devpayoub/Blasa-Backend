from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import history_for
from apps.rides.models import Ride
from apps.rides.services import NotRideOwner, RideError, staff_cancel_ride, staff_complete_ride
from core.schema import schema_or_anonymous

from . import services
from .models import Ban, Report
from .serializers import (
    AuditEntrySerializer,
    BanSerializer,
    ReportSerializer,
    RideOverrideInputSerializer,
    RideOverrideResultSerializer,
)
from .services import ModerationError


class ReportViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """POST: file a report (any user). GET: full report queue (staff only)."""

    serializer_class = ReportSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAdminUser()]
        return super().get_permissions()

    def get_queryset(self):
        if schema_or_anonymous(self):
            return Report.objects.none()
        return Report.objects.select_related("reporter").all()

    def create(self, request, *args, **kwargs):
        try:
            report = services.file_report(
                reporter=request.user,
                target_type=request.data.get("target_type"),
                target_id=request.data.get("target_id"),
                reason=request.data.get("reason"),
                details=request.data.get("details", ""),
            )
        except ModerationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ReportSerializer(report).data, status=status.HTTP_201_CREATED)


class ReportReviewView(APIView):
    """Staff decision on a report: DISMISSED or ACTIONED."""

    permission_classes = [IsAdminUser]

    def post(self, request, report_id):
        report = Report.objects.filter(pk=report_id).first()
        if report is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            report = services.review_report(
                report=report, decision=request.data.get("status"), staff=request.user,
                reason=request.data.get("reason", ""),
            )
        except ModerationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ReportSerializer(report).data)


class BanView(APIView):
    """Staff-only ban and unban."""

    permission_classes = [IsAdminUser]

    def post(self, request, user_id):
        try:
            ban = services.ban_user(user_id=user_id, reason=request.data.get("reason", ""), staff=request.user)
        except ModerationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(BanSerializer(ban).data, status=status.HTTP_201_CREATED)

    def delete(self, request, user_id):
        services.unban_user(user_id=user_id, staff=request.user, reason=request.data.get("reason", ""))
        return Response(status=status.HTTP_204_NO_CONTENT)


class BanListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        bans = Ban.objects.select_related("user").all()
        return Response(BanSerializer(bans, many=True).data)


class RideOverrideView(APIView):
    """Staff investigation/override for a stranded trip.

    Goes through the same ride/booking/ledger services as the owner actions, so
    seats, refunds and sponsor reversals stay consistent; only the ownership
    check is skipped and the decision is audited.
    """

    permission_classes = [IsAdminUser]

    @extend_schema(request=RideOverrideInputSerializer, responses={200: RideOverrideResultSerializer})
    def post(self, request, ride_id):
        ride = Ride.objects.filter(pk=ride_id).first()
        if ride is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        action = request.data.get("action")
        reason = request.data.get("reason", "")
        try:
            if action == "cancel":
                ride = staff_cancel_ride(ride=ride, staff=request.user, reason=reason)
            elif action == "complete":
                ride = staff_complete_ride(ride=ride, staff=request.user, reason=reason)
            else:
                return Response({"detail": "Action must be cancel or complete"}, status=status.HTTP_400_BAD_REQUEST)
        except NotRideOwner as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except RideError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"id": str(ride.id), "status": ride.status})


class AuditTrailView(APIView):
    """Staff read of one object's decision history (no request bodies or secrets)."""

    permission_classes = [IsAdminUser]

    @extend_schema(responses=AuditEntrySerializer(many=True))
    def get(self, request, object_type, object_id):
        entries = history_for(object_type, object_id)
        return Response(AuditEntrySerializer(entries, many=True).data)
