# V5.1 Deep Briefing architecture

## Generation and playback

~~~text
iPhone current-local-time 05:00
  -> POST /v1/briefings/daily-refresh {local_date}
  -> freeze pipeline_start_utc and rolling 24-hour window
  -> collect YAML-configured news + fetch official GitHub Trending Today
  -> reject missing, future, and older-than-24h news timestamps
  -> normalize, deduplicate, cluster, score, and select AI Top 5 + general Top 5
  -> SQLite BEGIN IMMEDIATE daily + 20-hour rolling budget claim
  -> at most one combined /chat/completions request
  -> validate four-part analysis; deterministically fill missing GitHub rows
  -> Python date intro + news + GitHub ranks 1–10
  -> save complete briefing and audit metrics in SQLite

Alarm stopped
  -> GET /v1/briefings/latest/text (cache only)
  -> iPhone gets current-location weather at playback time
  -> iPhone combines backend text + deterministic weather text
  -> Speak Text
~~~

Backend weather architecture: none. There is no location request field, weather provider, weather
HTTP request, weather model, weather database table, weather setting, or backend weather outro.

## Freshness and source coverage

The service fixes window_end_utc at pipeline start and window_start_utc exactly 24 hours earlier.
The collector receives this lower bound, and the service independently applies the authoritative
inclusive filter before normalization. A source cannot bypass freshness by ignoring `since`.
Missing or timezone-naive timestamps, timestamps before the lower bound, and timestamps after the
fixed upper bound are rejected. The window never expands when supply is low.

AI, embodied AI, and robotics candidates must clear the deterministic minimum score. The final
section contains exactly the highest-ranked five when five qualifying candidates exist. Ranking is
lexicographic: impact, novelty, technical importance, then source authority. Low-impact research
papers are excluded below `editorial.ai_research_minimum_impact`; ordinary software churn is
demoted, and the selector never pads the list with weak stories. General news is independently
capped at five important stories.

The LLM gives every selected event a complete Event, Background, Impact, and What to watch
explanation. A 10–15 minute whole-briefing target and 200–300/300–500 Chinese-character event
guidance are soft editorial goals, never truncation rules. Completeness controls length. The spoken
result is a morning analyst briefing rather than a headline reader.

## GitHub Trending Today

GitHubTrendingProvider fetches `https://github.com/trending?since=daily` with no language filter.
The standard-library HTML parser preserves page order and returns the first ten valid repository
articles. GitHub is a Today ranking snapshot; repositories need not have been created in the news
24-hour window.

Rows are stored in additive `github_trending_snapshots` keyed by snapshot_date and rank. A fetch
failure may use only the requested local_date's cached rows. With no same-day snapshot, the spoken
section says the ranking is unavailable. It never uses an older snapshot or triggers another LLM
request.

The combined response may include one compact purpose, trending reason, and main technology per
input repo. Unknown repos are ignored and the
first valid duplicate wins. Python iterates the original ranks 1–10 and uses description, language,
and stars_today for every missing row. Thus LLM omission cannot remove or reorder a repository.

## One-request budget and failure model

The V3 invariants remain unchanged. `llm_daily_budget` has UNIQUE(purpose, local_date), and an
atomic `BEGIN IMMEDIATE` transaction also checks the newest claim is at least 20 hours old. The
claim commits before outbound LLM HTTP. Failure consumes it; there is no retry, repair, per-topic,
or per-repository request.

All-news-source failure happens before claim. After claim, any provider or validation failure saves
the deterministic date + fresh news + GitHub fallback. The previous complete cache remains readable
until a replacement is fully saved. Startup and both GET endpoints are always cache-only.

## Metrics and spoken structure

Pipeline metrics include fixed window bounds, stale/missing/future rejection counts, fresh item
count, GitHub fetch success/count/snapshot date, existing selection/category counts, LLM calls,
tokens, latency, fallback reason, and final duration.

Backend spoken order is deterministic: date intro, important fresh news, natural AI/technology/
embodied coverage, then GitHub ranks 1–10. It ends after the GitHub section without a sign-off so the
iPhone can naturally append “最后来看一下天气……” at actual playback time.
