# Morning Intelligence Harness

A single-process FastAPI service that prefetches RSS and Hacker News, clusters publications into real-world events, ranks those events with deterministic personalized scoring, and serves a grounded Chinese spoken briefing to iOS Shortcuts.

The important latency decision is simple: the alarm does not start a full internet research job. A background refresh maintains ranked events and a ready briefing in SQLite. The normal iPhone path is one authenticated SQLite read from `GET /v1/briefings/latest/text`.

## What is implemented

- RSS/Atom and Hacker News source adapters behind one async protocol.
- Per-source timeout and partial-failure isolation.
- Canonical URL and normalized-title deduplication.
- Cross-source fuzzy title clustering into `NewsEvent`.
- Configurable hybrid recency, quality, confirmation, interest, novelty and optional LLM scoring.
- OpenAI-compatible batch scoring and spoken-script provider.
- Deterministic Chinese fallback when no API key is set or the LLM fails.
- SQLite persistence for items, events, briefings and pipeline runs.
- Startup/periodic prefetch, cached wake path and bounded incremental refresh.
- FastAPI JSON/text contracts, bearer/query-token auth and Docker deployment.
- Unit, integration, failure and eight-case offline evaluation suite.

Architecture and provenance are documented in [docs/architecture.md](docs/architecture.md), [docs/reference-audit.md](docs/reference-audit.md), and [docs/decisions/001-build-vs-reuse.md](docs/decisions/001-build-vs-reuse.md).

## Local development

```bash
git clone YOUR_REPOSITORY_URL morning-intelligence
cd morning-intelligence

cp .env.example .env
# Edit .env and set MORNING_API_TOKEN.
# LLM_API_KEY is optional; empty uses the deterministic fallback.

uv sync --extra dev
uv run uvicorn morning_intelligence.main:app --reload
```

The committed `config/sources.yaml` and `config/preferences.yaml` are runnable defaults. Edit feeds, interests and weights there; keep personal secrets in `.env`.

Test the API:

```bash
curl http://localhost:8000/health

curl -H "Authorization: Bearer $MORNING_API_TOKEN" \
  http://localhost:8000/v1/briefings/latest/text

curl -X POST \
  -H "Authorization: Bearer $MORNING_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"wake","target_minutes":5,"incremental_refresh":true}' \
  http://localhost:8000/v1/briefings/generate
```

The very first briefing requires a source fetch. Later GET requests are the low-latency cached path. For the best wake-up experience, keep the service running so the background refresher has populated the cache.

On the 2026-08-17 development smoke run with the committed four public source adapters, a cold no-LLM request completed in 2.24 seconds and the next cached GET in 1.6 milliseconds. These are observations, not service-level guarantees; network, host and feed performance vary.

## Docker

```bash
cp .env.example .env
# Edit .env before exposing the service.
docker compose up -d --build
docker compose logs -f morning-intelligence
```

SQLite lives in `./data` and configuration is mounted read-only from `./config`.

## Quality gates

```bash
uv run pytest
uv run ruff check .
uv run mypy
uv run python -m eval.runner
```

With the server running:

```bash
MORNING_API_TOKEN=your-token ./scripts/smoke_test.sh
```

The evaluation fixtures cover cross-source deduplication, distinct-event retention, tracking URLs, partial source failure, LLM fallback, no-news behavior, novelty and local processing latency.

## Configuration

Environment variables control secrets, file paths and operational timeouts. YAML controls sources and preferences. Ranking weights must sum to `1.0`; invalid configuration fails at startup rather than silently changing editorial behavior.

If `LLM_API_KEY` is provided, `LLM_BASE_URL` must expose the OpenAI-compatible `/chat/completions` endpoint. Event importance is scored in one batch and the final script is one additional call—never one call per article.

## iPhone setup

Follow [docs/ios-shortcut-setup.md](docs/ios-shortcut-setup.md). The final flow is:

```text
Alarm Is Stopped -> Run Morning Brief -> HTTP GET -> Speak Text
```

## Operational notes

- Run one Uvicorn worker. The scheduler is in-process; multiple workers would each refresh.
- Put a TLS reverse proxy in front of the service before exposing it publicly.
- Bearer headers are safer than query tokens.
- Docker disables Uvicorn access logs so a query-token fallback is not written to its default request log. Apply equivalent redaction at any reverse proxy.
- The application logs run IDs and counts, not API tokens or authorization headers.
- A cached briefing remains available if feeds or the LLM are down.

## Current gaps

- No server-side TTS/audio files; iOS Speak Text is the MVP output.
- No embeddings, Reddit, GitHub releases or deep-dive web research.
- Fuzzy title clustering is intentionally conservative and language-dependent.
- The in-process scheduler is designed for a single service process, not a distributed deployment.
- Deterministic fallback preserves source wording and does not translate English headlines; configure an LLM for fully natural Chinese synthesis.
