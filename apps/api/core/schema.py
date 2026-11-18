"""Shared schema-generation helpers."""


def schema_or_anonymous(view) -> bool:
    """True when drf-spectacular is probing the view without a real request.

    ViewSets whose get_queryset filters on request.user raise for AnonymousUser
    during schema generation; calling code guards with this helper and returns
    Model.objects.none() instead.
    """
    if getattr(view, "swagger_fake_view", False):
        return True
    user = getattr(view.request, "user", None)
    return user is None or not user.is_authenticated
