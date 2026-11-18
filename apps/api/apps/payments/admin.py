from django.contrib import admin

from .models import PaymentIntent, WebhookEvent


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "kind", "gateway", "amount", "status", "gateway_reference"]
    list_filter = ["gateway", "status", "kind"]
    search_fields = ["user__phone_number", "gateway_reference"]


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ["event_id", "gateway", "outcome", "processed_at", "created_at"]
    list_filter = ["gateway", "outcome"]
    search_fields = ["event_id"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
