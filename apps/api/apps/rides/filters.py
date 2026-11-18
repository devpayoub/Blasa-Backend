import django_filters
from django import forms
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.db.models import Exists, OuterRef

from apps.users.models import Profile

from .models import Ride

DEFAULT_RADIUS_KM = 10
WHEN_STATUSES = {
    "upcoming": [Ride.Status.OPEN, Ride.Status.FULL],
    "active": [Ride.Status.IN_PROGRESS],
    "completed": [Ride.Status.COMPLETED],
    "cancelled": [Ride.Status.CANCELLED],
}
ENDPOINTS = (("from", "pickup"), ("to", "drop-off"))


class RideSearchForm(forms.Form):
    def clean(self):
        data = super().clean()
        for prefix, name in ENDPOINTS:
            lat, lng = data.get(f"{prefix}_lat"), data.get(f"{prefix}_lng")
            if (lat is None) != (lng is None):
                raise forms.ValidationError(f"Send both {prefix}_lat and {prefix}_lng for the {name} point")
            if data.get(f"{prefix}_radius_km") is not None and lat is None:
                raise forms.ValidationError(f"{prefix}_radius_km needs a {name} point")
        after, before = data.get("departure_after"), data.get("departure_before")
        if after and before and before <= after:
            raise forms.ValidationError("departure_before must be later than departure_after")
        if data.get("ordering") == "pickup" and data.get("from_lat") is None:
            raise forms.ValidationError("Sorting by pickup distance needs a pickup point")
        return data


def _coordinate(limit):
    return django_filters.NumberFilter(method="_deferred", min_value=-limit, max_value=limit)


def _radius():
    return django_filters.NumberFilter(method="_deferred", min_value=0.5, max_value=100)


class RideSearchFilter(django_filters.FilterSet):
    """Discovery filters. Both endpoints must match; distance is straight-line, not a road route."""

    from_lat = _coordinate(90)
    from_lng = _coordinate(180)
    from_radius_km = _radius()
    to_lat = _coordinate(90)
    to_lng = _coordinate(180)
    to_radius_km = _radius()
    departure_after = django_filters.IsoDateTimeFilter(field_name="departure_at", lookup_expr="gte")
    departure_before = django_filters.IsoDateTimeFilter(field_name="departure_at", lookup_expr="lte")
    max_price = django_filters.NumberFilter(field_name="price_per_seat", lookup_expr="lte", min_value=0)
    min_seats = django_filters.NumberFilter(field_name="seats_available", lookup_expr="gte", min_value=1)
    when = django_filters.ChoiceFilter(choices=[(k, k) for k in WHEN_STATUSES], method="_when")
    shared_interests = django_filters.BooleanFilter(
        method="_shared", label="true: only drivers who share at least one of your interests",
    )
    atmosphere = django_filters.ChoiceFilter(
        choices=Ride.Atmosphere.choices, method="_atmosphere",
        label="Matches rides with this atmosphere or none set",
    )
    ordering = django_filters.ChoiceFilter(
        choices=[("departure", "departure"), ("price", "price"), ("pickup", "pickup")], method="_deferred",
    )

    class Meta:
        model = Ride
        fields = []
        form = RideSearchForm

    def _deferred(self, queryset, name, value):
        return queryset

    def _when(self, queryset, name, value):
        return queryset.filter(status__in=WHEN_STATUSES[value])

    def _shared(self, queryset, name, value):
        if not value:
            return queryset
        user = getattr(self.request, "user", None)
        mine = Profile.interests.through.objects.filter(profile__user=user).values("interesttag_id")
        # A member with no interests shares nothing, so the filter returns no rides rather than all of them.
        return queryset.filter(
            Exists(Profile.interests.through.objects.filter(profile__user=OuterRef("driver"), interesttag_id__in=mine))
        )

    def _atmosphere(self, queryset, name, value):
        return queryset.filter(atmosphere__in=[value, ""])

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        data = self.form.cleaned_data
        for prefix, field in (("from", "origin"), ("to", "destination")):
            if data.get(f"{prefix}_lat") is None:
                continue
            point = Point(float(data[f"{prefix}_lng"]), float(data[f"{prefix}_lat"]), srid=4326)
            radius = float(data.get(f"{prefix}_radius_km") or DEFAULT_RADIUS_KM)
            queryset = queryset.filter(**{f"{field}__distance_lte": (point, D(km=radius))})
            if prefix == "from":
                queryset = queryset.annotate(distance=Distance("origin", point))
        ordering, when = data.get("ordering"), data.get("when")
        if ordering == "price":
            return queryset.order_by("price_per_seat", "departure_at", "id")
        if ordering == "pickup":
            return queryset.order_by("distance", "departure_at", "id")
        if ordering == "departure" or when in ("upcoming", "active"):
            return queryset.order_by("departure_at", "id")
        if when:
            return queryset.order_by("-departure_at", "id")
        return queryset
