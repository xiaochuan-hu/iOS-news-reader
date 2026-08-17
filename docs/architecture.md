# Architecture

## Product path

```text
iOS Alarm Is Stopped
        |
        v
Personal Automation -> Morning Brief Shortcut
        |
        v
GET /v1/briefings/latest/text  (fast cached path)
        |
        +-- stale/missing -> POST /v1/briefings/generate
                              |
                              v
                     bounded incremental refresh
                              |
                              v
                         spoken_text
        |
        v
iOS Speak Text
```

## Service boundaries

```text
RSS / Hacker News adapters
          |
          v
    CollectorService             per-source timeout/failure isolation
          |
          v
     EventProcessor              normalize -> exact URL dedup -> title/fuzzy cluster
          |
          v
     RankingService              deterministic hybrid score -> shortlist
          |
          v
      BriefingAgent              optional batch LLM -> grounded spoken script fallback
          |
          v
      BriefingStore              SQLite items, events, briefings, pipeline runs
          |
          v
        FastAPI                  JSON and text endpoints, bearer/query-token auth
```

`NewsItem` is one publication or source record. `NewsEvent` is one real-world event supported by one or more items. Only events are ranked and spoken, preventing Reuters/BBC/technology-site coverage of the same event from being read three times.

## Two-stage latency design

Stage A runs at startup and on a configurable interval. It fetches, processes, ranks and caches events, then pre-generates the latest briefing. Stage B is the alarm path. `GET /latest/text` performs only authentication plus one SQLite read. `POST /generate` begins with cached ranked events; an optional incremental refresh is bounded by the request deadline. If refresh or synthesis fails, the newest cached briefing is returned with `is_fallback=true`.

The application is intentionally one process and one scheduler task. SQLite uses WAL and short transactions. A refresh lock prevents overlapping runs.

## Pipeline

1. Fetch enabled adapters concurrently with individual HTTP timeouts.
2. Normalize text, timestamps and canonical URLs.
3. Remove exact canonical-URL and normalized-title duplicates.
4. Cluster remaining items by token-aware fuzzy title similarity; embeddings are not required.
5. Aggregate source diversity, categories and time range into `NewsEvent`.
6. Compute deterministic scores and shortlist before any LLM request.
7. Optionally ask the configured LLM for importance and one grounded script in batch.
8. Validate and sanitize output; fall back to deterministic Chinese spoken prose.
9. Persist the briefing and mark its event IDs as spoken for novelty scoring.

## Hybrid ranking

All components are normalized to `[0, 1]`:

```text
final_score =
    0.25 * recency
  + 0.20 * source_quality
  + 0.20 * cross_source_confirmation
  + 0.20 * personal_interest
  + 0.10 * novelty
  + 0.05 * llm_importance
```

Weights are configuration-driven and validated to sum to one. Recency decays with age. Source quality is the mean configured quality of supporting sources. Cross-source confirmation saturates as independent sources increase. Personal interest uses configured category weights without hard-coded user preferences. Novelty is reduced when a matching event was spoken recently. Missing LLM scoring uses a neutral zero and does not prevent ranking.

## Configuration

Secrets and deployment settings come from environment variables. Source lists and user preferences come from YAML. Example files are safe to commit; real tokens and `.env` are ignored. Settings remain deliberately small: database path, timeouts/deadline, refresh interval, feed list, HN limits, score weights, similarity threshold, source quality, interests and briefing limits.

## Failure model

- One source failure: record it and continue with successful sources.
- All sources fail: retain cached events; return the cached briefing if available.
- LLM timeout/invalid JSON: deterministic score and script.
- No significant events: say that there are no sufficiently important updates; never invent filler.
- Stale cache: expose freshness in JSON; text remains available while a later refresh retries.
- Every external operation has a timeout; logs redact tokens and authorization headers.

## Observability

Each `PipelineRun` stores a run ID, timestamps, requested/succeeded/failed source counts, fetched/deduped/clustered/shortlisted counts, LLM call count/latency, total latency, briefing character count, estimated duration and error summaries. Structured application logs include the run ID and numeric metrics, never secrets.

## Security

All `/v1/*` endpoints require `MORNING_API_TOKEN` when configured. `Authorization: Bearer` is preferred; `?token=` exists for Shortcuts compatibility and should only be used over HTTPS because query strings are more likely to appear in logs. `/health` is unauthenticated and reveals no configuration.

## Deferred work

Server TTS, embeddings, deep-dive multi-agent research, Reddit/GitHub/OpenBB sources, distributed queues, a native iOS app and multi-worker scheduling are outside this MVP.
