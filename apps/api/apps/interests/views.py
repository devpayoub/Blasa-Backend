from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from core.schema import schema_or_anonymous

from . import services
from .models import Interest
from .serializers import InterestSerializer


class InterestViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = InterestSerializer

    def get_queryset(self):
        if schema_or_anonymous(self):
            return Interest.objects.none()
        queryset = Interest.objects.filter(passenger=self.request.user).order_by("-created_at", "-id")
        if self.action == "list":
            queryset = queryset.filter(is_active=True)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            interest = services.create_interest(passenger=request.user, **serializer.validated_data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InterestSerializer(interest).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        interest = get_object_or_404(Interest, pk=kwargs["pk"], passenger=request.user)
        try:
            services.cancel_interest(interest=interest, actor=request.user)
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(status=status.HTTP_204_NO_CONTENT)
