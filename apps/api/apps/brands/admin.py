from django.contrib import admin

from .models import Brand, Campaign, SponsoredRide


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "owner", "created_at"]
    search_fields = ["name"]


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ["id", "brand", "name", "budget", "spent", "is_active", "ends_at"]
    list_filter = ["is_active"]


@admin.register(SponsoredRide)
class SponsoredRideAdmin(admin.ModelAdmin):
    list_display = ["id", "campaign", "ride", "created_at"]
