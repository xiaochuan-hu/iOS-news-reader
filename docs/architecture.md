# V3 architecture

## Two independent iPhone flows

~~~text
Generation
iPhone current local time 05:00
  -> authenticated POST /v1/briefings/daily-refresh {local_date}
  -> collect, normalize, deduplicate, cluster, score, select
  -> SQLite atomic daily + rolling interval claim
  -> one combined OpenAI-compatible /chat/completions request
  -> validate or deterministically fall back
  -> INSERT complete briefing in SQLite

Playback
Any alarm is stopped
  -> device-hour guard 05:00–08:59
  -> authenticated GET /v1/briefings/latest/text
  -> SQLite cache read
  -> iOS Speak Text
~~~

The flows do not call each other. The iPhone system timezone owns the 05:00 schedule, so travel
automatically follows Asia/Shanghai, Asia/Singapore, Europe/London, or the current US timezone.
Railway does not infer the location and server timezone does not define the production schedule.

## Before and after

V2 used an in-process Railway timezone scheduler, generated on an empty startup, allowed GET to
generate on cache miss, and normally called the LLM once for event synthesis plus once for spoken
composition.

V3 defaults to client-triggered generation. Startup and GET never invoke an LLM. Deterministic
selection is unchanged, but one combined editorial request returns structured intelligence and
the final spoken narrative. A persistent fail-closed budget is claimed before that request.

## Persistent budget and concurrency

The additive llm_daily_budget table contains:

~~~text
id TEXT PRIMARY KEY
purpose TEXT NOT NULL
local_date TEXT NOT NULL
claimed_at_utc TEXT NOT NULL
completed_at_utc TEXT NULL
success INTEGER NULL
error TEXT NOT NULL
provider TEXT NOT NULL
model TEXT NOT NULL
UNIQUE(purpose, local_date)
~~~

The repository opens BEGIN IMMEDIATE, checks the unique local date, checks the newest claim against
MORNING_MIN_LLM_INTERVAL_HOURS (minimum/default 20), inserts the claim, and commits. Only after that
commit may the provider send HTTP. SQLite's write lock makes this safe across concurrent requests
and processes; asyncio.Lock only avoids duplicate collection work inside one process.

Completion fields are observability only. HTTP 400/500, timeout, malformed output, validation
failure, cancellation, or a process crash never reopens the allowance. There is no automatic
provider retry or validation repair call.

## Combined editorial call

The input retains each selected event's ID, canonical title, first/latest timestamps, categories,
source count, deterministic/global/editorial scores, and each source's name, quality, title,
description and timestamp. Exactly duplicate descriptions are omitted while their source records
remain.

The JSON response contains events with summary_zh, why_it_matters, watch_next, key_facts,
confidence and source_consensus, plus spoken_text. Unknown event IDs are ignored, the first valid
duplicate wins, malformed rows are isolated, and missing rows use deterministic intelligence.
Spoken text is sanitized and checked for Chinese content and the configured 80–120 percent
character range. Invalid prose uses the deterministic script; it never triggers a repair call.

The prompt separates verified facts from interpretation and uncertainty, compares sources, avoids
unsupported model knowledge, gives major stories more depth, and asks for a natural approximately
five-minute Chinese narrative rather than numbered RSS summaries.

## Cache and failure model

The existing briefing is never deleted. Source work and LLM work happen before the new complete
briefing is inserted, so GET remains fast and may return yesterday's briefing during the 05:00
generation window. All-source failure happens before claim and may be retried later. Failure after
claim creates and saves a deterministic fresh fallback when possible; if the deterministic path
also fails, the previous cache remains.

An empty database starts normally: /health returns 200 and both latest endpoints return 503 until
the iPhone sends a successful daily refresh.

## Scheduler and manual endpoint

MORNING_GENERATION_TRIGGER=client is the production default. The legacy scheduler starts only when
trigger=server and MORNING_BACKGROUND_REFRESH_ENABLED=true; it no longer refreshes at startup and
uses the same budgeted service method. The old timezone/time variables apply only to that mode.

POST /v1/briefings/generate defaults to 409. If explicitly enabled for development, it enters the
same budgeted workflow and has no bypass.

## Metrics and invariants

Pipeline runs record llm_calls, llm_editorial_calls, prompt/completion/total tokens, prompt cache
hit/miss tokens when supplied, editorial latency, fallback reason, event/category counts, briefing
characters, duration and total latency. Legacy synthesis/script fields remain zero on V3.

The production invariants are:

1. GET endpoints never invoke an LLM.
2. Startup never invokes an LLM.
3. The claim is committed before outbound HTTP.
4. A failed attempt consumes the claim.
5. No automatic LLM retry or repair exists.
6. Each purpose/local_date has at most one claim.
7. Claims are at least 20 hours apart.
8. Concurrent attempts cannot both claim.
9. Location scheduling belongs to the iPhone.
10. One request produces both intelligence and spoken text.
