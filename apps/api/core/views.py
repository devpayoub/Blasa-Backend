"""Public entry points that are not part of the versioned API.

`/` is what a human sees when they open the deployed host, so it returns a small
self-contained status page instead of a 404. It deliberately touches no database
or cache: Render's health probe is `/healthz`, and this page should stay fast
even when Neon is scaled to zero.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_safe

SERVICE_NAME = "Blasa API"

# Hardcoded rather than reversed: these are public contract URLs, and a rename
# in urls.py should not silently turn the landing page's links into 500s.
LINKS = (
    {"label": "API reference", "href": "/api/docs/", "hint": "Swagger UI"},
    {"label": "OpenAPI schema", "href": "/api/schema/", "hint": "YAML"},
    {"label": "Health", "href": "/healthz", "hint": "JSON probe"},
    {"label": "Admin", "href": "/admin/", "hint": "Staff only"},
)


def _payload() -> dict:
    version = getattr(settings, "SPECTACULAR_SETTINGS", {}).get("VERSION", "1.0.0")
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "version": version,
        "environment": "development" if settings.DEBUG else "production",
        "api_base": "/api/v1/",
        "links": {item["label"]: item["href"] for item in LINKS},
    }


def _page(payload: dict) -> str:
    links = "".join(
        f'<a class="link" href="{item["href"]}">'
        f'<span class="link-label">{item["label"]}</span>'
        f'<span class="link-hint">{item["hint"]}</span></a>'
        for item in LINKS
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{payload["service"]}</title>
<link rel="icon" href="data:," />
<style>
  :root {{
    --navy: #13294b;
    --navy-deep: #0d1c33;
    --sand: #f0e7d5;
    --muted: #93a4bd;
    --green: #3ddc97;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    background: radial-gradient(circle at 20% 0%, #1b3a66 0%, var(--navy-deep) 55%);
    color: var(--sand);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.5;
  }}
  main {{
    width: 100%;
    max-width: 520px;
    background: rgba(19, 41, 75, 0.72);
    border: 1px solid rgba(240, 231, 213, 0.12);
    border-radius: 18px;
    padding: 32px;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
    backdrop-filter: blur(6px);
  }}
  h1 {{ margin: 0 0 6px; font-size: 26px; letter-spacing: -0.02em; }}
  .tagline {{ margin: 0 0 20px; color: var(--muted); font-size: 14px; }}
  .status {{
    display: inline-flex; align-items: center; gap: 8px;
    padding: 6px 12px; margin-bottom: 22px;
    border-radius: 999px;
    background: rgba(61, 220, 151, 0.12);
    border: 1px solid rgba(61, 220, 151, 0.3);
    color: var(--green); font-size: 13px; font-weight: 600;
  }}
  .dot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--green); }}
  dl {{ display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; margin: 0 0 24px; font-size: 13px; }}
  dt {{ color: var(--muted); }}
  dd {{ margin: 0; font-variant-numeric: tabular-nums; }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
  .links {{ display: grid; gap: 8px; }}
  .link {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 11px 14px;
    border-radius: 11px;
    background: rgba(240, 231, 213, 0.05);
    border: 1px solid rgba(240, 231, 213, 0.1);
    color: var(--sand); text-decoration: none; font-size: 14px;
    transition: background 120ms ease, border-color 120ms ease;
  }}
  .link:hover {{ background: rgba(240, 231, 213, 0.1); border-color: rgba(240, 231, 213, 0.25); }}
  .link-hint {{ color: var(--muted); font-size: 12px; }}
  footer {{ margin-top: 22px; color: var(--muted); font-size: 12px; }}
</style>
</head>
<body>
<main>
  <h1>{payload["service"]}</h1>
  <p class="tagline">Ride-sharing and carpooling for Tunisia.</p>
  <div class="status"><span class="dot"></span> API responding</div>
  <dl>
    <dt>Version</dt><dd>{payload["version"]}</dd>
    <dt>Environment</dt><dd>{payload["environment"]}</dd>
    <dt>Base path</dt><dd><code>{payload["api_base"]}</code></dd>
  </dl>
  <div class="links">{links}</div>
  <footer>All endpoints are served under <code>/api/v1/</code>. Amounts are TND in millimes.</footer>
</main>
</body>
</html>"""


@require_safe
def api_root(request):
    """Landing page at `/`. Returns JSON when the client asks for it."""
    payload = _payload()
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse(payload)
    return HttpResponse(_page(payload), content_type="text/html; charset=utf-8")
