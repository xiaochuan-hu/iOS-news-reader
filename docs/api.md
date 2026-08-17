# HTTP API

Base URL examples use `http://localhost:8000`. `/health` is public. All `/v1` routes require the configured token; bearer auth is preferred. The query-token fallback exists only for Shortcuts clients that cannot conveniently set a header and must only be used over HTTPS.

## Health

```http
GET /health
```

```json
{"status":"ok"}
```

## Latest cached briefing

```http
GET /v1/briefings/latest
Authorization: Bearer TOKEN
```

Returns `id`, `generated_at`, `freshness_minutes`, `event_count`, `estimated_duration_seconds`, `spoken_text`, structured `sections`, `sources` and `is_fallback`. `sections[].text` contains the Chinese event synthesis when the LLM path succeeded. If the database has no briefing yet, the first request attempts one deadline-bounded refresh.

## Shortcut text endpoint

```http
GET /v1/briefings/latest/text
Authorization: Bearer TOKEN
Accept: text/plain
```

Response content type is `text/plain; charset=utf-8`; the body is only `spoken_text`.

## Generate or refresh

```http
POST /v1/briefings/generate
Authorization: Bearer TOKEN
Content-Type: application/json
```

```json
{
  "mode": "wake",
  "target_minutes": 5,
  "incremental_refresh": true,
  "max_age_minutes": 30
}
```

When selected events are newer than `max_age_minutes`, the service skips source collection and synthesizes from cache. Otherwise it attempts a bounded refresh. `target_minutes` changes the spoken character budget and duration estimate. LLM-only failure produces a deterministic briefing without changing the response contract; a complete refresh failure returns the most recent briefing with `is_fallback=true` when one exists. With no cache it returns `503`.

Normal LLM generation uses two provider calls: one batch structured synthesis and one spoken composition. Unknown event IDs, malformed rows and missing optional fields are handled before the spoken call. No per-article calls are made.

## Errors

- `401`: missing/invalid token.
- `422`: invalid request body.
- `503`: generation failed or exceeded the deadline and no cached briefing exists.

Tokens and authorization headers are never included in application logs.
