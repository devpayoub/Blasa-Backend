from django.contrib import admin

from .models import IncentiveAward, IncentiveCampaign, Voucher


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ["id", "code", "amount", "issued_to", "expires_at", "redeemed_at"]
    search_fields = ["code"]
    list_filter = ["redeemed_at"]


@admin.register(IncentiveCampaign)
class IncentiveCampaignAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "amount", "is_active", "starts_at", "ends_at"]
    list_filter = ["kind", "is_active"]


@admin.register(IncentiveAward)
class IncentiveAwardAdmin(admin.ModelAdmin):
    list_display = ["code", "kind", "user", "amount", "status", "expires_at", "redeemed_at"]
    list_filter = ["kind", "status"]
    search_fields = ["code", "user__phone_number"]
    # Awards move money; status changes go through the redeem API so the ledger stays in step.
    readonly_fields = [f.name for f in IncentiveAward._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
