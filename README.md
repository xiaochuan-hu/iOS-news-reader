# Morning Intelligence Harness

A single-process FastAPI service that turns RSS and Hacker News into one grounded Chinese morning briefing. The iPhone triggers generation at its current local 05:00; Railway persists one fail-closed LLM allowance and the finished briefing in SQLite.

The alarm never starts research or an LLM request. It only reads `GET /v1/briefings/latest/text` from SQLite. Generation is a separate authenticated `POST /v1/briefings/daily-refresh` automation.

## What is implemented

- RSS/Atom and Hacker News source adapters behind one async protocol.
- Per-source timeout and partial-failure isolation.
- Canonical URL and normalized-title deduplication.
- Cross-source fuzzy title clustering into `NewsEvent`.
- Configurable deterministic ranking plus a diversity-aware editorial selection layer.
- Separate global importance and personal relevance, with soft category targets and no filler quota.
- One OpenAI-compatible combined editorial call producing event intelligence and spoken text.
- Validated Chinese event intelligence: summary, why it matters, watch next, key facts and confidence.
- Deterministic Chinese fallback when no API key is set or the LLM fails.
- SQLite persistence for items, events, briefings, pipeline runs and the daily LLM budget.
- Atomic local-calendar and rolling 20-hour budget gates before the outbound request.
- Startup-safe, cache-only wake path; restart, redeploy and duplicate triggers spend no quota.
- FastAPI JSON/text contracts, bearer/query-token auth and Docker deployment.
- Unit, integration, failure and sixteen-case offline evaluation suite.

Architecture and provenance are documented in [docs/architecture.md](docs/architecture.md), [docs/reference-audit.md](docs/reference-audit.md), [docs/decisions/001-build-vs-reuse.md](docs/decisions/001-build-vs-reuse.md), and [docs/decisions/003-local-daily-single-llm.md](docs/decisions/003-local-daily-single-llm.md).

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
  -d '{"local_date":"2026-08-19"}' \
  http://localhost:8000/v1/briefings/daily-refresh
```

With an empty database, both GET endpoints return `503` until the first daily POST succeeds. GET is
always the low-latency cache path and never invokes collection or an LLM. Application startup never
generates, whether the database is empty or populated.

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

If `LLM_API_KEY` is provided, `LLM_BASE_URL` must expose the OpenAI-compatible `/chat/completions` endpoint. Exactly one attempted request produces both validated event intelligence and spoken text. There is no retry or repair call. A normal day is one request, a 30-day month is about 30 requests, and every GET, restart, redeploy or duplicate daily trigger adds zero. With no key or any provider failure, the deterministic briefing remains available.

`MORNING_GENERATION_TRIGGER=client` is the safe default. The iPhone's system timezone owns 05:00;
Railway neither guesses location nor uses server time for the production trigger. The old IANA
timezone settings apply only when the explicitly enabled legacy `server` trigger is used.
`LLM_THINKING_MODE` is optional and provider-neutral: when unset, no `thinking` field is sent.

`briefing.target_minutes` and `briefing.characters_per_minute` define the requested character budget. `editorial.minimum_score` removes weak events before composition; `editorial.category_targets` are diversity hints rather than quotas. It is valid to return fewer than `briefing.min_events` when the remaining news is not meaningful.

## iPhone setup

Follow [docs/ios-shortcut-setup.md](docs/ios-shortcut-setup.md). The final flow is:

```text
iPhone local 05:00 -> POST daily-refresh -> SQLite
Alarm Is Stopped -> time guard 05:00–08:59 -> cached GET -> Speak Text
```

## Operational notes

- Run one Uvicorn worker. SQLite still makes budget claims safe across concurrent processes.
- Put a TLS reverse proxy in front of the service before exposing it publicly.
- Bearer headers are safer than query tokens.
- Docker disables Uvicorn access logs so a query-token fallback is not written to its default request log. Apply equivalent redaction at any reverse proxy.
- The application logs run IDs and counts, not API tokens or authorization headers.
- A cached briefing remains available if feeds or the LLM are down.

## Current gaps

- No server-side TTS/audio files; iOS Speak Text is the MVP output.
- No embeddings, Reddit, GitHub releases or deep-dive web research.
- Fuzzy title clustering is intentionally conservative and language-dependent.
- The optional legacy scheduler remains in-process; production uses the iPhone client trigger.
- Deterministic fallback preserves source wording and does not translate English headlines; configure an LLM for fully natural Chinese synthesis.
