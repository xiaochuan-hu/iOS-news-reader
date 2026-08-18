# ADR 003: Client-local daily trigger and one fail-closed editorial request

## Decision

The iPhone triggers POST /v1/briefings/daily-refresh at its current local 05:00 and supplies a
strict local calendar date. Railway does not schedule production generation by location.

After deterministic selection, SQLite atomically claims a morning_editorial allowance using both
UNIQUE(purpose, local_date) and a minimum 20-hour interval from the most recent claim. The claim is
committed before one combined OpenAI-compatible request generates structured event intelligence
and spoken text. Completion status never reopens a claim.

GET endpoints and application startup are cache-only. The legacy scheduler and manual endpoint are
disabled by default and, when explicitly enabled, use the same budgeted service layer.

## Consequences

- Travel follows the phone timezone without Railway location inference.
- Duplicate, concurrent, restart and failure paths cannot create a second same-day attempt.
- HTTP/provider failures require deterministic local fallback; automatic retry and repair calls are
  prohibited.
- The previous complete cache remains readable throughout generation.
- Normal usage falls from roughly two requests per day to one.
