# Production image for Render (and other Docker hosts).
#
# Differs from api.Dockerfile in two ways: it installs requirements/prod.txt
# instead of dev.txt (no pytest/ruff on a 512 MB free instance), and it drops
# root. GeoDjango needs GDAL/GEOS/PROJ at runtime, which is why the API cannot
# be deployed on Render's native Python runtime — it must run as a container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev binutils libproj-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/prod.txt

COPY . .

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
