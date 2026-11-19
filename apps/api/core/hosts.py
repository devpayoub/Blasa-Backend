"""Host allow-list assembly.

Kept out of settings.py so it can be unit-tested: importing a settings module
runs it once at startup, which makes its behaviour awkward to assert.
"""

from __future__ import annotations


def build_allowed_hosts(configured, render_hostname: str = "") -> list[str]:
    """Return the ALLOWED_HOSTS list for this process.

    `configured` comes from DJANGO_ALLOWED_HOSTS. Render sets
    RENDER_EXTERNAL_HOSTNAME to the service's public hostname, so a rename or a
    fresh service no longer locks the API out of its own URL with DisallowedHost.
    """
    hosts = [host.strip() for host in (configured or []) if host and host.strip()]
    render_hostname = (render_hostname or "").strip()
    if render_hostname and render_hostname not in hosts:
        hosts.append(render_hostname)
    return hosts
