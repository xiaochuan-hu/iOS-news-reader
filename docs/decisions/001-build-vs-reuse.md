# ADR 001: Build a focused service; reuse patterns, not repositories

- Status: accepted
- Date: 2026-08-17

## Context

The six audited projects cover aggregation, general agents, iOS automations, voice UX, TTS production and headless research. None provides the required combination of an explicit event layer, deterministic personalized ranking, alarm-time cached delivery, FastAPI contract and SQLite replay prevention.

## Decision

Build Morning Intelligence Harness as an independent Python service. Do not add any reference repository as a dependency, subtree, submodule or history import. Implement clean-room equivalents of the useful patterns recorded in `docs/reference-audit.md`.

The service uses:

1. source adapters returning one `NewsItem` model;
2. deterministic canonical URL, normalized-title and fuzzy event clustering;
3. `NewsEvent` as the unit of scoring, synthesis and replay history;
4. deterministic hybrid ranking before any optional batch LLM work;
5. SQLite as the system of record and latest-artifact cache;
6. background prefetch plus a bounded incremental wake refresh;
7. plain text delivery to native iOS Speak Text;
8. an OpenAI-compatible provider with a deterministic no-key fallback.

## Consequences

- The MVP stays single-process, inexpensive and testable without network or LLM credentials.
- External failures can be isolated per source and per optional LLM call.
- Some broad features from the references are intentionally absent: server TTS, multi-agent research, Redis/Celery, a general tool framework, twenty source types and native iOS code.
- The scheduler is an in-process periodic task suitable for one worker. Multi-worker deployment would require an external singleton scheduler or one dedicated refresh process.
- No copied reference code means no third-party notice is required for our source, while the audit still preserves provenance of the design research.
