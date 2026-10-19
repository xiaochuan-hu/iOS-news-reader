FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN uv sync --no-dev

RUN useradd --create-home --uid 10001 morning \
    && mkdir -p /app/data \
    && chown -R morning:morning /app

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

USER morning
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "uvicorn morning_intelligence.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
