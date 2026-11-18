from django.db.models import Count, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from rest_framework import serializers


def completed_trips(user_ref: str):
    """Rides driven plus bookings travelled to completion, for the user at ``user_ref``."""
    from apps.bookings.models import Booking
    from apps.bookings.state_machine import BookingStatus
    from apps.rides.models import Ride

    def count(qs, field):
        return Coalesce(
            Subquery(
                qs.order_by().values(field).annotate(n=Count("id")).values("n")[:1], output_field=IntegerField(),
            ),
            Value(0),
        )

    driven = Ride.objects.filter(driver=OuterRef(user_ref), status=Ride.Status.COMPLETED)
    ridden = Booking.objects.filter(passenger=OuterRef(user_ref), status=BookingStatus.COMPLETED)
    return count(driven, "driver") + count(ridden, "passenger")


def display_name(user) -> str:
    """First name and last initial; other members never see a full name, phone or email."""
    profile = getattr(user, "profile", None)
    parts = (profile.full_name if profile else "").split()
    if not parts:
        return "Blasa member"
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0].upper()}."


class UserSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    display_name = serializers.CharField()
    average_rating = serializers.DecimalField(max_digits=3, decimal_places=2)
    completed_trips = serializers.IntegerField()
    shared_interests = serializers.ListField(
        child=serializers.SlugField(), help_text="Interest slugs this member shares with you; empty for yourself",
    )


def viewer_interests(context: dict) -> frozenset[str]:
    """The requesting member's interest slugs, cached on the serializer context for the whole response."""
    if "_viewer_interests" not in context:
        request = context.get("request")
        user = getattr(request, "user", None)
        profile = getattr(user, "profile", None) if user and user.is_authenticated else None
        context["_viewer_interests"] = (
            frozenset(profile.interests.values_list("slug", flat=True)) if profile else frozenset()
        )
    return context["_viewer_interests"]


def summary_of(user, trips: int | None, context: dict | None = None) -> dict:
    if trips is None:
        from apps.users.models import User

        trips = User.objects.filter(pk=user.pk).annotate(n=completed_trips("pk")).values_list("n", flat=True)[0]
    profile = getattr(user, "profile", None)
    request = (context or {}).get("request")
    shared = []
    if profile and context is not None and getattr(request, "user", None) != user:
        mine = viewer_interests(context)
        # .all() reuses a prefetch of profile__interests when the queryset has one.
        shared = [tag.slug for tag in profile.interests.all() if tag.slug in mine] if mine else []
    return {
        "id": user.id,
        "display_name": display_name(user),
        "average_rating": profile.average_rating if profile else 0,
        "completed_trips": trips,
        "shared_interests": shared,
    }


REVIEW_LIMIT = 10


def _shares_a_booking(viewer, target) -> bool:
    """Participants of the same booking (either direction) may read each other's reviews."""
    from django.db.models import Q

    from apps.bookings.models import Booking

    if viewer is None or not getattr(viewer, "is_authenticated", False):
        return False
    if viewer.pk == target.pk:
        return True
    return Booking.objects.filter(
        Q(passenger=target, ride__driver=viewer) | Q(passenger=viewer, ride__driver=target)
    ).exists()


def profile_detail(user, viewer) -> dict:
    """Public-safe participant profile: trust numbers, declared bio and interests only.

    Never returns phone, email, identity documents, wallet data or another
    member's booking identifiers. Review comments are limited to members who
    actually shared a booking with this person; everyone else sees the aggregate.
    """
    from apps.ratings.models import Rating

    profile = getattr(user, "profile", None)
    ratings = Rating.objects.filter(ratee=user)
    review_count = ratings.count()
    is_self = getattr(viewer, "pk", None) == user.pk
    can_read_reviews = is_self or _shares_a_booking(viewer, user)
    reviews = []
    if can_read_reviews:
        reviews = [
            {
                "score": rating.score,
                "comment": rating.comment,
                "created_at": rating.created_at,
                "rater_display_name": display_name(rating.rater),
            }
            for rating in ratings.select_related("rater", "rater__profile").order_by("-created_at")[:REVIEW_LIMIT]
        ]
    trips = completed_trips_of(user)
    mine = set()
    if not is_self and profile is not None:
        viewer_profile = getattr(viewer, "profile", None)
        if viewer_profile is not None:
            mine = set(viewer_profile.interests.values_list("slug", flat=True))
    return {
        "id": user.id,
        "display_name": display_name(user),
        "bio": profile.bio if profile else "",
        "member_since": user.created_at,
        "is_driver": user.role == user.Role.DRIVER,
        "average_rating": profile.average_rating if profile else 0,
        "review_count": review_count,
        "completed_trips": trips,
        "shared_interests": [tag.slug for tag in profile.interests.all() if tag.slug in mine] if mine else [],
        "reviews_visible": can_read_reviews,
        "reviews": reviews,
        "is_self": is_self,
    }


def completed_trips_of(user) -> int:
    from apps.users.models import User

    return User.objects.filter(pk=user.pk).annotate(n=completed_trips("pk")).values_list("n", flat=True).first() or 0
