# Railway deployment

V5.1 Deep Briefing and Morning Intelligence Audio Playback V2 Phase 1 keep the existing HTTPS,
Bearer auth, Docker image, persistent `/app/data` volume and SQLite path. The image now includes
ffmpeg for MP3 concatenation. It does not add a Docker VOLUME directive and does not require Redis,
a queue, or an external scheduler.

## Production flow

~~~text
iPhone current-local-time 05:00 automation
  -> POST /v1/briefings/daily-refresh
  -> strict 24h news + GitHub Trending Today Top10
  -> deterministic AI Top 5 + general Top 5 editorial selection
  -> persistent atomic allowance
  -> one deep-analysis /chat/completions request with no duration compression target
  -> SQLite /app/data/morning_intelligence.db
  -> in-process background Azure Speech chunk synthesis
  -> atomic MP3 cache /app/data/audio/<text-and-config-sha256>.mp3

Alarm stopped
  -> authenticated GET /v1/briefings/latest/audio-url
  -> short-lived HMAC-signed public media URL
  -> iOS media player streams the Railway MP3
  -> Shortcut may terminate; no permanent phone media file is required
~~~

The iPhone timezone controls 05:00. Railway must not use Europe/London or server time as the V3
production clock.

## Volume and process permissions

Attach a Railway Volume to the API service at:

~~~text
/app/data
~~~

Keep `MORNING_DATABASE_PATH=/app/data/morning_intelligence.db` and
`MORNING_AUDIO_DIRECTORY=/app/data/audio`. Both SQLite and generated MP3 files must reside on the
same persistent `/app/data` Railway Volume. Railway mounts Volumes as root, so
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

Add two independent secrets:

~~~dotenv
MORNING_MEDIA_SIGNING_KEY=<new independent random secret>
AZURE_SPEECH_KEY=<Azure Speech resource key>
~~~

Use at least 32 random bytes for `MORNING_MEDIA_SIGNING_KEY` and do not reuse `MORNING_API_TOKEN`.
The API token remains in the authenticated request header and is never embedded in a media URL.
Azure Speech credentials are separate from DeepSeek/OpenAI-compatible credentials and must not be
printed in logs or committed.

Set or verify:

~~~dotenv
MORNING_DATABASE_PATH=/app/data/morning_intelligence.db
MORNING_SOURCES_CONFIG=config/sources.yaml
MORNING_PREFERENCES_CONFIG=config/preferences.yaml
MORNING_SOURCE_TIMEOUT_SECONDS=8
MORNING_AUXILIARY_TIMEOUT_SECONDS=8
MORNING_LLM_TIMEOUT_SECONDS=60
MORNING_GENERATION_DEADLINE_SECONDS=180

MORNING_GENERATION_TRIGGER=client
MORNING_BACKGROUND_REFRESH_ENABLED=false
MORNING_MANUAL_GENERATION_ENABLED=false
MORNING_MIN_LLM_INTERVAL_HOURS=20

MORNING_TTS_ENABLED=true
MORNING_TTS_PROVIDER=azure
MORNING_TTS_VOICE=zh-CN-YunxiNeural
MORNING_TTS_STYLE=newscast
MORNING_TTS_RATE=0%
MORNING_TTS_TIMEOUT_SECONDS=45
MORNING_AUDIO_DIRECTORY=/app/data/audio
MORNING_AUDIO_RETENTION_DAYS=7
MORNING_MEDIA_URL_TTL_SECONDS=14400
AZURE_SPEECH_REGION=<resource region, for example uksouth>

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

Audio synthesis is outside the editorial LLM path: it does not claim or consume the DeepSeek daily
budget. An Azure/ffmpeg failure is logged as a warning and does not make a successfully persisted
text briefing partial or failed. The authenticated audio endpoints return 503 while audio is
unavailable; `/latest`, `/latest/text`, and `/daily-refresh` retain their existing behavior.

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
13. Request `/v1/briefings/latest/audio-url` with Bearer auth. If it returns 202/preparing, honor
    `Retry-After` and retry; the endpoint never waits for Azure. Once it returns 200/ready, verify
    the signed URL supports unauthenticated GET, HEAD, and a byte Range request as `audio/mpeg`.
14. Confirm the URL contains no `MORNING_API_TOKEN`, expires after four hours, and the MP3 exists
    only under `/app/data/audio` on the persistent volume.
15. Redeploy/restart once and verify the text cache, audio cache, and budget claim survive with zero
    startup calls.

No automated development test contacts api.deepseek.com. The first production combined request
must be an intentional, manually observed E2E check. Automated audio tests use fake providers and
HTTP transports; the first real Azure request must likewise be an intentional production check.

## 05:00 race

Generation may take seconds. If an alarm is stopped at 05:00:05, the cache-only text GET can
correctly return the previous complete briefing while the new one is still being generated. Text
GET never waits and never generates. The audio URL endpoint schedules missing audio in the
background and returns 202 without waiting.
To make the new briefing and its background audio more likely to be ready, place the wake alarm at
05:02 or 05:05, or trigger generation a few minutes before the alarm; the required local 05:00
automation remains supported.

## Rollback

The change adds no database migration and older briefing rows are unchanged. A code rollback can
continue reading them. Do not delete or recreate the database. Cached MP3 files are derived data;
they can remain on the volume across rollback and will age out after the configured retention period
when audio generation runs again.
