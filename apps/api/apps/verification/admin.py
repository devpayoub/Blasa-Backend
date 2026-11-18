from django.contrib import admin, messages
from rest_framework.exceptions import PermissionDenied

from .models import VerificationDocument, VerificationReview
from .services import review_document, revoke_document


class VerificationReviewInline(admin.TabularInline):
    model = VerificationReview
    fields = ["actor", "decision", "reason", "timestamp"]
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(VerificationDocument)
class VerificationDocumentAdmin(admin.ModelAdmin):
    list_display = ["user", "doc_type", "status", "reviewed_by", "created_at"]
    list_filter = ["doc_type", "status"]
    search_fields = ["user__phone_number"]
    readonly_fields = [
        "user", "doc_type", "storage_key", "content_type", "size_bytes", "uploaded_at", "status",
        "reviewed_by", "reviewed_at", "rejection_reason", "created_at", "updated_at",
    ]
    inlines = [VerificationReviewInline]
    actions = ["approve_selected", "reject_selected", "request_more_selected", "revoke_selected"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def _review_selected(self, request, queryset, decision, reason=""):
        for doc in queryset.order_by("user_id", "pk"):
            try:
                review_document(document=doc, reviewer=request.user, decision=decision, reason=reason)
            except (ValueError, PermissionDenied) as exc:
                self.message_user(request, f"{doc.pk}: {exc}", level=messages.ERROR)

    @admin.action(description="Approve selected confirmed documents")
    def approve_selected(self, request, queryset):
        self._review_selected(request, queryset, VerificationReview.Decision.APPROVE)

    @admin.action(description="Reject selected documents")
    def reject_selected(self, request, queryset):
        self._review_selected(request, queryset, VerificationReview.Decision.REJECT, "Rejected by admin")

    @admin.action(description="Request more documents")
    def request_more_selected(self, request, queryset):
        self._review_selected(request, queryset, VerificationReview.Decision.REQUEST_MORE, "More documents requested by admin")

    @admin.action(description="Revoke selected approved documents")
    def revoke_selected(self, request, queryset):
        for doc in queryset.order_by("user_id", "pk"):
            try:
                revoke_document(document=doc, reviewer=request.user, reason="Revoked by admin")
            except (ValueError, PermissionDenied) as exc:
                self.message_user(request, f"{doc.pk}: {exc}", level=messages.ERROR)
