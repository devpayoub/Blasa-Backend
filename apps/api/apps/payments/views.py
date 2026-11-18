from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.schema import schema_or_anonymous

from .gateways import GatewayError
from .models import PaymentIntent
from .serializers import PaymentIntentSerializer, TopupSerializer
from .services import IntentStateError, TopupFailed, cancel_intent, create_topup, handle_webhook


class TopupView(APIView):
    """Create a wallet topup. Staff cash settles immediately; Flouci returns a payment URL."""

    def post(self, request):
        serializer = TopupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data["gateway"] == PaymentIntent.Gateway.CASH and not request.user.is_staff:
            return Response(
                {"detail": "Cash topups require staff reconciliation."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            intent, data = create_topup(
                user=request.user,
                amount=serializer.validated_data["amount"],
                gateway=serializer.validated_data["gateway"],
            )
        except TopupFailed as exc:
            return Response(
                {"detail": "The payment provider could not start this payment. Nothing was charged.",
                 "intent": PaymentIntentSerializer(exc.intent).data},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        payload = PaymentIntentSerializer(intent).data
        payload.update(data)
        return Response(payload, status=status.HTTP_201_CREATED)


class WebhookView(APIView):
    """Inbound gateway notifications. Signature-verified and idempotent; anonymous by design."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, gateway: str):
        try:
            event = handle_webhook(
                gateway=gateway,
                body=request.body,
                signature=request.headers.get("X-Flouci-Signature"),
            )
        except GatewayError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if event is None:
            return Response({"detail": "Already processed"})
        return Response({"detail": "Processed"})


class PaymentIntentListView(generics.ListAPIView):
    """The caller's own payment attempts, newest first."""

    serializer_class = PaymentIntentSerializer

    def get_queryset(self):
        if schema_or_anonymous(self):
            return PaymentIntent.objects.none()
        return PaymentIntent.objects.filter(user=self.request.user).order_by("-created_at")


class PaymentIntentDetailView(generics.RetrieveAPIView):
    serializer_class = PaymentIntentSerializer

    def get_queryset(self):
        if schema_or_anonymous(self):
            return PaymentIntent.objects.none()
        return PaymentIntent.objects.filter(user=self.request.user)


class PaymentIntentCancelView(APIView):
    """Abandon a pending payment. A later trusted success still credits the wallet."""

    def post(self, request, pk):
        intent = generics.get_object_or_404(PaymentIntent, pk=pk, user=request.user)
        try:
            intent = cancel_intent(intent=intent)
        except IntentStateError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(PaymentIntentSerializer(intent).data)
