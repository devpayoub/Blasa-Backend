# Production image for Render (and other Docker hosts).
#
# IMPORTANT — build context: Render builds with the REPOSITORY ROOT as the
# Docker build context, not apps/api (it ignores dockerContext for this
# service, which is why a context-relative COPY failed with
# '"/requirements": not found'). Every COPY below is therefore repo-root
# relative. Build it locally with:
#
#     docker build -t blasa-api .
#
# The development image is a separate file, infra/docker/api.Dockerfile, and is
# only used by docker compose.
#
# GeoDjango needs GDAL/GEOS/PROJ at runtime, which is why the API cannot run on
# Render's native Python runtime — it must be a container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev binutils libproj-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first so the pip layer survives application-code changes.
COPY apps/api/requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/prod.txt

# Everything under apps/api becomes the app root: manage.py, config/, apps/.
COPY apps/api/ ./

# Render runs containers as an arbitrary uid; the app never writes to /app at
# runtime except staticfiles, which collectstatic creates here.
RUN useradd --create-home --uid 10001 blasa \
    && mkdir -p /app/staticfiles \
    && chown -R blasa:blasa /app
USER blasa

EXPOSE 8000

# Overridden by render.yaml's dockerCommand, which also runs migrate and
# collectstatic. Kept here so `docker run` works standalone.
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "--workers", "1"]
