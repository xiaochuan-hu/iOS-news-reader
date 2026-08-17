FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN uv sync --no-dev

RUN useradd --create-home --uid 10001 morning \
    && mkdir -p /app/data \
    && chown -R morning:morning /app

USER morning
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
VOLUME ["/app/data"]
EXPOSE 8000

CMD ["uvicorn", "morning_intelligence.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
