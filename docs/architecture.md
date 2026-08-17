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
     RankingService              deterministic global + personal scores
          |
          v
    EditorialSelector            threshold -> diversity -> editorial order
          |
          v
      BriefingAgent              structured Chinese intelligence -> spoken composer
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
6. Compute deterministic ranking scores plus a global-importance score independent of personal taste.
7. Apply the editorial minimum, diversity utility and soft category targets; select at most 6–8 meaningful events.
8. In one LLM call, synthesize all selected evidence into validated Chinese event intelligence.
9. In one additional LLM call, compose the intelligence into natural spoken Chinese.
10. Validate and sanitize output; fall back deterministically on no key, timeout, malformed JSON or invalid prose.
11. Persist the briefing and mark its event IDs as spoken for novelty scoring.

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

Weights are configuration-driven and validated to sum to one. Recency decays with age. Source quality is the mean configured quality of supporting sources. Cross-source confirmation saturates as independent sources increase. Personal interest uses configured category weights without hard-coded user preferences. Novelty is reduced when a matching event was spoken recently.

## V2 editorial selection

Selection does not use `sorted(events)[:N]`. First, weak candidates are removed by the editorial minimum. For each survivor:

```text
global_importance = 0.45 * recency + 0.35 * source_quality + 0.20 * confirmation
editorial_score = 0.75 * global_importance + 0.25 * personal_relevance
```

Configured avoid categories apply an additional penalty. A greedy utility then rewards a new category and source diversity, and progressively penalizes exceeding that category's soft target. Targets never admit an event below the global threshold. The highest global-importance event leads; the remainder receive a stable topic-aware order.

This keeps major world news eligible even when it is not a preferred topic, while preventing a run of catchy single-source developer stories from consuming the briefing.

## V2 LLM pipeline

Call 1 receives compact evidence for every selected `NewsEvent` and returns JSON keyed by known event IDs. Rows are validated independently into `EventIntelligence`: `summary_zh`, `why_it_matters`, `watch_next`, `key_facts`, `confidence` and `source_consensus`. Unknown IDs are ignored, malformed rows are isolated, and missing events receive deterministic evidence text.

Call 2 sees only those validated intelligence objects—not the raw article list—and writes plain spoken Chinese. The prompt defines a target character range from `target_minutes * characters_per_minute`, asks for unequal story depth and natural transitions, and prohibits URLs, Markdown, citations and unsupported facts. Prompts have explicit version constants.

## Configuration

Secrets and deployment settings come from environment variables. Source lists and user preferences come from YAML. Example files are safe to commit; real tokens and `.env` are ignored. Settings remain deliberately small: database path, timeouts/deadline, refresh interval, feed list, HN limits, score weights, similarity threshold, source quality, interests and briefing limits.

## Failure model

- One source failure: record it and continue with successful sources.
- All sources fail: retain cached events; return the cached briefing if available.
- LLM synthesis timeout/invalid JSON: skip the spoken call and use the deterministic script.
- LLM spoken output failure or non-Chinese narrative: sanitize and use the deterministic script.
- No significant events: say that there are no sufficiently important updates; never invent filler.
- Stale cache: expose freshness in JSON; text remains available while a later refresh retries.
- Every external operation has a timeout; logs redact tokens and authorization headers.

## Observability

Each `PipelineRun` stores candidate/selected counts and categories, separate synthesis/script call counts and latencies, fallback reason, source and event counts, total latency, briefing characters, estimated duration and error summaries. Structured application logs include the run ID and aggregate metrics, never evidence text or secrets.

## Security

All `/v1/*` endpoints require `MORNING_API_TOKEN` when configured. `Authorization: Bearer` is preferred; `?token=` exists for Shortcuts compatibility and should only be used over HTTPS because query strings are more likely to appear in logs. `/health` is unauthenticated and reveals no configuration.

## Deferred work

Server TTS, embeddings, deep-dive multi-agent research, Reddit/GitHub/OpenBB sources, distributed queues, a native iOS app and multi-worker scheduling are outside this MVP.
