from django.contrib import admin

from .models import InterestTag, Profile, User


@admin.register(InterestTag)
class InterestTagAdmin(admin.ModelAdmin):
    list_display = ["slug", "label_en", "label_fr", "label_ar", "sort_order", "is_active"]
    list_editable = ["sort_order", "is_active"]


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    filter_horizontal = ["interests"]


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ["contact", "role", "is_verified", "is_active", "date_joined"]
    list_filter = ["role", "is_verified", "is_active"]
    search_fields = ["phone_number", "email", "profile__full_name"]
    readonly_fields = ["phone_verified_at", "email_verified_at"]
    inlines = [ProfileInline]

    @admin.display(description="Contact")
    def contact(self, obj):
        return str(obj)
