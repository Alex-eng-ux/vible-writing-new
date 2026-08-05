# Backend image for both the API and the idle worker.
# Fixed runtime, pinned dependencies, no secrets baked into the image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml ./pyproject.toml
COPY alembic.ini ./alembic.ini
COPY app ./app

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# API_BIND_SCOPE selects the bind host inside app.main.run().
CMD ["python", "-m", "app.main"]