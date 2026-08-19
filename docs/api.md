# HTTP API

/health is public. Every /v1 route uses the existing Bearer authentication. Examples omit
the Railway hostname.

## Health

~~~http
GET /health
~~~

Returns {"status":"ok"}. Startup performs no source or LLM work.

## Latest cached briefing

~~~http
GET /v1/briefings/latest
Authorization: Bearer TOKEN
~~~

Returns the existing JSON contract plus additive news_window_start, news_window_end, and
github_trending_count metadata.

~~~http
GET /v1/briefings/latest/text
Authorization: Bearer TOKEN
Accept: text/plain
~~~

Returns only cached spoken_text as text/plain; charset=utf-8. Both GET handlers perform one
repository read. They never collect sources, generate, wait for an in-flight refresh, or invoke an
LLM. If there is no persisted briefing, they return 503 with
"briefing has not been generated yet".

## Production daily refresh

~~~http
POST /v1/briefings/daily-refresh
Authorization: Bearer TOKEN
Content-Type: application/json

{"local_date":"2026-08-19"}
~~~

local_date must be strict YYYY-MM-DD and within one calendar day before or after the current UTC
date. This accommodates every world timezone without asking Railway to know the iPhone's timezone
and rejects arbitrary dates that could bypass the calendar gate.

The endpoint collects and deterministically selects news before atomically claiming
morning_editorial in SQLite. Responses are HTTP 200 and idempotent:

~~~json
{
  "status": "generated",
  "local_date": "2026-08-19",
  "briefing_id": "…",
  "generated_at": "2026-08-18T21:00:18Z",
  "budget_reason": ""
}
~~~

Statuses are generated, generated_fallback, already_claimed, no_llm_available, or cached. A
duplicate date or a different date less than 20 hours after the last claim returns already_claimed
and sends no LLM request. budget_reason distinguishes already_claimed from min_interval.

All-source failure occurs before the claim. It returns the prior cache when available, otherwise
503; a later POST may retry collection. Any failure after claim consumes that claim permanently.
Provider failure produces and saves a deterministic fresh fallback without retrying the LLM.

The request body has exactly one field: local_date. Unknown fields are rejected. In particular,
there is no weather_location: the backend has no weather functionality.

News items must satisfy the inclusive window_start <= published_at <= pipeline_start rule. GitHub
repositories use a separate freshness concept: the official global Trending Today page snapshot.

## Legacy manual generation

~~~http
POST /v1/briefings/generate
~~~

This existing endpoint is disabled by default with 409 Conflict. Setting
MORNING_MANUAL_GENERATION_ENABLED=true is intended only for development. Even then it routes
through the same daily budget and has no force, retry, repair, admin override, or bypass parameter.

## Errors

- 401: missing or invalid API token.
- 409: manual generation is disabled.
- 422: malformed or out-of-window local_date, or another invalid body.
- 503: no cache exists for a cache-only GET, or refresh failed before a usable result.

Tokens, API keys and Authorization headers are never included in application logs.
