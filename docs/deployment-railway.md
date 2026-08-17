# Railway deployment

This deployment keeps the iPhone API contract unchanged while removing the always-on Mac:

```text
iPhone Alarm Stop
    -> iOS Shortcut
    -> HTTPS Railway service
    -> GET cached morning briefing
    -> iOS Speak Text
```

The production data flow is:

```text
Railway persistent FastAPI service
    -> scheduled background generation
    -> RSS / Hacker News
    -> editorial pipeline
    -> DeepSeek synthesis
    -> DeepSeek spoken composition
    -> SQLite /app/data/morning_intelligence.db
    -> iPhone GET cached text
```

The scheduler runs in the single FastAPI process. It generates immediately only when the
database has no briefing, then waits for the next configured local daily time. A redeploy with
an existing briefing does not spend another LLM call. `ZoneInfo` applies the IANA timezone and
DST rules. Change `MORNING_REFRESH_TIMEZONE` to another IANA name such as `Asia/Singapore`
without changing code.

## Railway service configuration

Deploy one service named `api` from this repository's `feat/railway-deployment` branch. The
root `railway.toml` selects the Dockerfile builder, `/health`, a 300-second healthcheck timeout,
and the on-failure restart policy. The Docker command binds `0.0.0.0` and `${PORT:-8000}`.

Create a Railway Volume attached to `api` with mount path:

```text
/app/data
```

Set the database path to `/app/data/morning_intelligence.db`. Railway mounts Volumes as root,
so `RAILWAY_RUN_UID=0` lets the image entrypoint assign `/app/data` to UID 10001 before it drops
privileges. Uvicorn and the application still run as the non-root `morning` user. Local Docker
does not need this variable and defaults to port 8000.

Configure these Railway Variables. Values marked `<secret>` must be entered in Railway and
must never be committed:

```dotenv
MORNING_API_TOKEN=<secret>
MORNING_DATABASE_PATH=/app/data/morning_intelligence.db
MORNING_SOURCES_CONFIG=config/sources.yaml
MORNING_PREFERENCES_CONFIG=config/preferences.yaml
MORNING_SOURCE_TIMEOUT_SECONDS=8
MORNING_LLM_TIMEOUT_SECONDS=60
MORNING_GENERATION_DEADLINE_SECONDS=90
MORNING_BACKGROUND_REFRESH_ENABLED=true
MORNING_REFRESH_TIMEZONE=Europe/London
MORNING_REFRESH_TIME_LOCAL=06:00

LLM_API_KEY=<secret>
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_THINKING_MODE=disabled

RAILWAY_RUN_UID=0
```

`OpenAICompatibleProvider` remains provider-neutral. The `thinking` request field is sent only
when `LLM_THINKING_MODE` is explicitly `enabled` or `disabled`; omit the variable for providers
that do not implement it.

Generate a Railway public domain for `api`. The production endpoint is:

```text
https://<railway-domain>/v1/briefings/latest/text
```

Requests keep the same authorization header:

```http
Authorization: Bearer <MORNING_API_TOKEN>
```

The normal GET reads the cached SQLite briefing and should not wait for the LLM. Use
`POST /v1/briefings/generate` only for a manual refresh, debugging, or emergency regeneration.
Uvicorn access logs are disabled so the supported query-token fallback is not written to the
default access log; Bearer authentication remains preferred.

## Deployment and acceptance checks

Install the current Railway CLI, run `railway setup agent`, authenticate in the browser when
prompted, and create or link the `morning-intelligence` project. Set secrets with the CLI's
`railway variable set KEY --stdin` form so they are not command-line arguments. Configure the
non-secret variables above, attach the Volume before the production generation test, deploy,
then run `railway domain`.

Verify these endpoints without exposing token values:

1. `GET /health` returns `200` without authentication.
2. `GET /v1/briefings/latest/text` without or with a wrong token returns `401`.
3. Authenticated `POST /v1/briefings/generate` completes both LLM stages without fallback.
4. Authenticated `GET /v1/briefings/latest/text` returns Chinese `text/plain` from cache.
5. Record the briefing ID and timestamp, restart or redeploy, and confirm they remain present.

Railway's current documentation covers the [config-as-code schema](https://docs.railway.com/config-as-code/reference),
[`PORT` healthchecks](https://docs.railway.com/deployments/healthchecks),
[persistent Volume paths and permissions](https://docs.railway.com/volumes), and
[`railway setup agent`](https://docs.railway.com/cli/setup).

## iPhone Shortcut switch

Change only the URL field:

```text
http://192.168.5.50:8000/v1/briefings/latest/text
-> https://<railway-domain>/v1/briefings/latest/text
```

Keep `GET`, the Authorization Bearer header, its existing token, and Speak Text unchanged.
