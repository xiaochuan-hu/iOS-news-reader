# ADR 004: Strict-fresh AI and GitHub briefing; weather stays on iPhone

## Decision

Morning Intelligence V4 applies an authoritative inclusive rolling 24-hour publication filter to
all news, expands verified AI/technology/embodied-AI/robotics feeds, and captures GitHub's official
global Trending Today Top 10 during daily refresh.

One combined LLM request may produce event intelligence, news prose, and grounded repository
summaries. Python computes the date/weekday, preserves GitHub rank, fills omitted repositories from
scraped metadata, and composes the final cached backend text. The V3 persistent daily and 20-hour
rolling budget remains the only LLM gate.

Weather is deliberately outside the backend. At actual playback, the iPhone obtains current-
location weather through Apple Shortcuts, formats it deterministically, appends it to cached backend
text, and speaks the result.

## Consequences

- Stale, missing-time, and future-dated news cannot reach ranking or the LLM; low supply shortens
  the briefing instead of widening the window.
- GitHub Today and news published-within-24h remain distinct freshness concepts.
- All ten parsed repositories are spoken exactly once and in page order even if the LLM omits rows.
- GitHub failure is isolated and may use only a same-date SQLite snapshot.
- No weather location crosses the API boundary, no Railway weather variables are needed, and the
  forecast reflects the user's location and time at playback.
- GET, startup, duplicate POST, fallback, and concurrent-request invariants from ADR 003 remain.
