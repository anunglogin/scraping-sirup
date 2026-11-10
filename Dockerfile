# One image for every service; docker-compose picks the command.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Jakarta

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install . && rm -rf /root/.cache

COPY alembic.ini ./
COPY db/migrations ./db/migrations

USER app

EXPOSE 8000
CMD ["uvicorn", "sirup.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
