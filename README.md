# Morning Intelligence Harness

A single-process FastAPI service that prefetches RSS and Hacker News, clusters publications into real-world events, ranks those events with deterministic personalized scoring, and serves a grounded Chinese spoken briefing to iOS Shortcuts.

The important latency decision is simple: the alarm does not start a full internet research job. A background refresh maintains ranked events and a ready briefing in SQLite. The normal iPhone path is one authenticated SQLite read from `GET /v1/briefings/latest/text`.

## What is implemented

- RSS/Atom and Hacker News source adapters behind one async protocol.
- Per-source timeout and partial-failure isolation.
- Canonical URL and normalized-title deduplication.
- Cross-source fuzzy title clustering into `NewsEvent`.
- Configurable deterministic ranking plus a diversity-aware editorial selection layer.
- Separate global importance and personal relevance, with soft category targets and no filler quota.
- OpenAI-compatible batch event synthesis followed by one Chinese spoken-script call.
- Validated Chinese event intelligence: summary, why it matters, watch next, key facts and confidence.
- Deterministic Chinese fallback when no API key is set or the LLM fails.
- SQLite persistence for items, events, briefings and pipeline runs.
- Cold-start/daily scheduled prefetch, cached wake path and bounded incremental refresh.
- FastAPI JSON/text contracts, bearer/query-token auth and Docker deployment.
- Unit, integration, failure and sixteen-case offline evaluation suite.

Architecture and provenance are documented in [docs/architecture.md](docs/architecture.md), [docs/reference-audit.md](docs/reference-audit.md), [docs/decisions/001-build-vs-reuse.md](docs/decisions/001-build-vs-reuse.md), and [docs/decisions/002-editorial-llm-pipeline.md](docs/decisions/002-editorial-llm-pipeline.md).

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

The very first briefing requires a source fetch. Later GET requests are the low-latency cached
path. The background scheduler refreshes once per configured local day and follows IANA timezone
DST rules; a restart with an existing cached briefing does not trigger another LLM call.

On the 2026-08-17 V2 development smoke run with the committed four public source adapters, a cold no-LLM request completed in 1.68 seconds, selected six events from 32 candidates, and the next cached GET completed in 1.5 milliseconds. These are observations, not service-level guarantees; network, host and feed performance vary.

## Docker

```bash
cp .env.example .env
# Edit .env before exposing the service.
docker compose up -d --build
docker compose logs -f morning-intelligence
```

SQLite lives in `./data` and configuration is mounted read-only from `./config`.
For production deployment, persistent storage, variables, healthchecks, and the iPhone URL switch
are documented in [docs/deployment-railway.md](docs/deployment-railway.md).

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

The evaluation fixtures cover the V1 regressions plus English-to-Chinese synthesis, AI-topic balance, soft targets, malformed JSON, LLM timeout fallback, grounding rules, partial structured output and target-length response.

## Configuration

Environment variables control secrets, file paths and operational timeouts. YAML controls sources and preferences. Ranking weights must sum to `1.0`; invalid configuration fails at startup rather than silently changing editorial behavior.

If `LLM_API_KEY` is provided, `LLM_BASE_URL` must expose the OpenAI-compatible `/chat/completions` endpoint. One request synthesizes all selected events into validated Chinese intelligence and one request writes the spoken briefing. Normal generation therefore uses at most two calls, never one call per article. With no key or any provider failure, the deterministic briefing remains available.

`MORNING_REFRESH_TIMEZONE` accepts any IANA timezone, such as `Europe/London` or
`Asia/Singapore`; change the environment variable without changing code. The local refresh time
uses strict `HH:MM`. `LLM_THINKING_MODE` is optional and provider-neutral: when unset, no
`thinking` field is sent.

`briefing.target_minutes` and `briefing.characters_per_minute` define the requested character budget. `editorial.minimum_score` removes weak events before composition; `editorial.category_targets` are diversity hints rather than quotas. It is valid to return fewer than `briefing.min_events` when the remaining news is not meaningful.

## iPhone setup

Follow [docs/ios-shortcut-setup.md](docs/ios-shortcut-setup.md). The final flow is:

```text
Alarm Is Stopped -> Run Morning Brief -> HTTP GET -> Speak Text
```

## Operational notes

- Run one Uvicorn worker. The daily scheduler is in-process; multiple workers would each refresh.
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
