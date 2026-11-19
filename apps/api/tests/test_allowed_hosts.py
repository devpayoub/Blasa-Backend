"""ALLOWED_HOSTS assembly.

A stale DJANGO_ALLOWED_HOSTS is the single most common cause of a Render deploy
that boots cleanly and then answers every request with DisallowedHost, so the
merge with Render's injected hostname is covered explicitly.
"""

from core.hosts import build_allowed_hosts


def test_keeps_configured_hosts():
    assert build_allowed_hosts(["blasa-backend.onrender.com", "localhost"]) == [
        "blasa-backend.onrender.com",
        "localhost",
    ]


def test_appends_the_render_hostname():
    hosts = build_allowed_hosts(["localhost"], "blasa-backend.onrender.com")

    assert hosts == ["localhost", "blasa-backend.onrender.com"]


def test_does_not_duplicate_the_render_hostname():
    hosts = build_allowed_hosts(
        ["blasa-backend.onrender.com"], "blasa-backend.onrender.com"
    )

    assert hosts == ["blasa-backend.onrender.com"]


def test_render_hostname_alone_is_enough():
    # prod.py refuses to boot on an empty list; Render always supplies one.
    assert build_allowed_hosts([], "blasa-backend.onrender.com") == [
        "blasa-backend.onrender.com"
    ]


def test_empty_inputs_stay_empty():
    assert build_allowed_hosts([], "") == []
    assert build_allowed_hosts(None, "") == []


def test_blank_entries_are_dropped():
    # A trailing comma or stray spaces in DJANGO_ALLOWED_HOSTS must not widen
    # the allow-list with an empty or whitespace host.
    assert build_allowed_hosts(["", "localhost", "  "], " ") == ["localhost"]
