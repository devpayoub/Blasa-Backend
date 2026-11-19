"""The public landing page at `/`."""

import pytest
from django.test import Client

HTML = "text/html"


@pytest.mark.django_db
def test_root_serves_a_landing_page_for_browsers():
    response = Client().get("/")

    assert response.status_code == 200
    assert response["Content-Type"].startswith(HTML)
    body = response.content.decode()
    assert "Blasa API" in body
    # The links an operator actually needs from the host root.
    for href in ("/api/docs/", "/api/schema/", "/healthz", "/admin/"):
        assert f'href="{href}"' in body
    assert "/api/v1/" in body


@pytest.mark.django_db
def test_root_returns_json_when_asked():
    response = Client().get("/", headers={"accept": "application/json"})

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    data = response.json()
    assert data["service"] == "Blasa API"
    assert data["status"] == "ok"
    assert data["api_base"] == "/api/v1/"
    assert data["links"]["Health"] == "/healthz"


@pytest.mark.django_db
def test_root_does_not_leak_configuration():
    body = Client().get("/").content.decode()

    for secret in ("DATABASE_URL", "postgres", "BREVO", "xkeysib", "AWS_", "SENTRY_DSN", "SECRET_KEY"):
        assert secret not in body


@pytest.mark.django_db
def test_root_answers_head_requests():
    # Uptime checkers and link scanners commonly probe with HEAD.
    response = Client().head("/")

    assert response.status_code == 200
