from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.response import Response

from apps.verification.serializers import ErrorSerializer
from core.schema import schema_or_anonymous

from . import services
from .models import Vehicle
from .serializers import VehicleSerializer


@extend_schema_view(
    create=extend_schema(
        request=VehicleSerializer,
        responses={201: VehicleSerializer, 400: ErrorSerializer},
        description="Active drivers only; optional owned Carte Grise. Verification is derived from document status.",
    ),
    update=extend_schema(
        request=VehicleSerializer, responses={200: VehicleSerializer, 400: ErrorSerializer, 404: ErrorSerializer},
        description="Atomic owner/active-driver update. is_active is editable; carte_grise=null detaches it.",
    ),
    partial_update=extend_schema(
        request=VehicleSerializer, responses={200: VehicleSerializer, 400: ErrorSerializer, 404: ErrorSerializer},
        description="Atomic PATCH: validate ownership/type before any mutations. null detaches; omission retains Carte Grise.",
    ),
)
class VehicleViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Drivers manage their own vehicles. All writes go through services; no hard deletes."""

    serializer_class = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if schema_or_anonymous(self):
            return Vehicle.objects.none()
        return Vehicle.objects.filter(driver=self.request.user).select_related("carte_grise", "driver").order_by("-created_at", "id")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            vehicle = services.register_vehicle(driver=request.user, **serializer.validated_data)
        except ValueError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(self.get_serializer(vehicle).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            instance = services.update_vehicle(
                vehicle=instance, driver=request.user, **serializer.validated_data,
            )
        except ValueError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(self.get_serializer(instance).data)
