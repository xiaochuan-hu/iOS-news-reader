# Railway deployment

V4 keeps the existing HTTPS, Bearer auth, Docker image, persistent /app/data volume and SQLite
path. It does not add a Docker VOLUME directive and does not require Redis, a queue, or an external
scheduler.

## Production flow

~~~text
iPhone current-local-time 05:00 automation
  -> POST /v1/briefings/daily-refresh
  -> strict 24h news + GitHub Trending Today Top10
  -> deterministic editorial selection
  -> persistent atomic allowance
  -> one DeepSeek /chat/completions request
  -> SQLite /app/data/morning_intelligence.db

Alarm stopped
  -> GET /v1/briefings/latest/text
  -> cached backend text
  -> iPhone current-location weather -> combine -> Speak Text
~~~

The iPhone timezone controls 05:00. Railway must not use Europe/London or server time as the V3
production clock.

## Volume and process permissions

Attach a Railway Volume to the API service at:

~~~text
/app/data
~~~

Keep MORNING_DATABASE_PATH=/app/data/morning_intelligence.db. Railway mounts Volumes as root, so
RAILWAY_RUN_UID=0 lets docker-entrypoint.sh give /app/data to UID 10001 before dropping to the
non-root morning user. Do not add VOLUME to the Dockerfile.

Repository initialization only executes CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS.
The V4 github_trending_snapshots migration is additive; existing briefings and V3 budget rows
remain readable during and after deployment. No weather table is created.

## Variables to check manually before deploy

Codex does not modify Railway variables. Retain the two existing secrets without printing or
changing their values:

~~~dotenv
MORNING_API_TOKEN=<existing secret>
LLM_API_KEY=<existing secret>
~~~

Set or verify:

~~~dotenv
MORNING_DATABASE_PATH=/app/data/morning_intelligence.db
MORNING_SOURCES_CONFIG=config/sources.yaml
MORNING_PREFERENCES_CONFIG=config/preferences.yaml
MORNING_SOURCE_TIMEOUT_SECONDS=8
MORNING_AUXILIARY_TIMEOUT_SECONDS=8
MORNING_LLM_TIMEOUT_SECONDS=60
MORNING_GENERATION_DEADLINE_SECONDS=90

MORNING_GENERATION_TRIGGER=client
MORNING_BACKGROUND_REFRESH_ENABLED=false
MORNING_MANUAL_GENERATION_ENABLED=false
MORNING_MIN_LLM_INTERVAL_HOURS=20

LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_THINKING_MODE=disabled

RAILWAY_RUN_UID=0
~~~

Important: production may currently have MORNING_BACKGROUND_REFRESH_ENABLED=true. Change it to
false. The new client trigger default is an additional safety boundary: an old true value alone
cannot start the legacy scheduler, but both values should still reflect production intent.

MORNING_REFRESH_TIMEZONE and MORNING_REFRESH_TIME_LOCAL may remain for legacy server-scheduled
development, but they have no effect when MORNING_GENERATION_TRIGGER=client.

No Railway weather or location variable is required. Weather is an iPhone Shortcut responsibility.

## Deployment steps

1. Confirm the target branch and quality gates locally.
2. Take or validate a backup of the persistent SQLite volume using the existing Railway process.
3. Manually update the non-secret variables above while retaining existing secret values.
4. Deploy the reviewed commit through the normal Railway workflow.
5. Confirm /health is 200 before triggering generation.
6. Confirm an unauthenticated protected request is 401.
7. Confirm the previously persisted briefing ID/text still reads successfully.
8. Inspect startup logs: there must be no DeepSeek request or pipeline generation.
9. From an authenticated test, POST today's iPhone-formatted local date exactly once.
10. Confirm the window metrics, github_trending_count=10 when GitHub is available, llm_calls=1,
    and one /chat/completions request.
11. Repeat the same POST and confirm already_claimed with zero extra calls.
12. Repeatedly GET latest/text and confirm no extra calls.
13. Redeploy/restart once and verify the cache and budget claim survive with zero startup calls.

No automated development test contacts api.deepseek.com. The first production combined request
must be an intentional, manually observed E2E check.

## 05:00 race

Generation may take seconds. If an alarm is stopped at 05:00:05, GET can correctly return the
previous complete briefing while the new one is still being generated. GET never waits and never
generates. To make the new briefing more likely to be ready, place the wake alarm at 05:02 or 05:05,
or trigger generation a few minutes before the alarm; the required local 05:00 automation remains
supported.

## Rollback

The migration is additive and older briefing rows are unchanged. A code rollback can continue
reading them. Do not delete or recreate the database. The new budget table should be retained even
if code is rolled back so audit history is not lost.
