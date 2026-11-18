from django.contrib import admin

from .models import Account, LedgerEntry, Transaction


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ["code", "kind", "currency", "allow_negative"]
    search_fields = ["code"]


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    readonly_fields = ["account", "amount"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ["reference", "kind", "created_at"]
    search_fields = ["reference"]
    inlines = [LedgerEntryInline]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
