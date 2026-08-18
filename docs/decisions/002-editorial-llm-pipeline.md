# ADR 002: Separate editorial selection, event intelligence and spoken composition

Status: superseded by ADR 003 for the production LLM composition and trigger path. The deterministic
selection and enriched event boundary remain in use.

- Status: accepted
- Date: 2026-08-17

## Context

V1 proved the iPhone-to-cache path but its deterministic top-N composition could overrepresent one category and read English source wording directly. A single broad LLM prompt would combine factual synthesis, selection and radio writing, making grounding, partial failure and call count difficult to control.

## Decision

Keep collection, clustering, storage, API and scheduling unchanged. Add three explicit stages after deterministic scoring:

1. `EditorialSelector` filters by a global minimum and selects with global importance, personal relevance, source evidence and soft category diversity.
2. One OpenAI-compatible call converts all selected events into validated `EventIntelligence` objects.
3. One OpenAI-compatible call converts only those validated objects into plain spoken Chinese.

Global importance receives 75% of the editorial score and personal relevance 25%. Category targets alter selection utility but never override the minimum score. The normal path is capped at two LLM calls. A synthesis failure skips call two and immediately uses deterministic output; a spoken-call failure does the same.

`EnrichedNewsEvent` is separate from the collected `NewsEvent`. This avoids treating generated interpretation as raw evidence and allows missing or malformed LLM rows to receive deterministic evidence-derived fields.

## Consequences

- Major news can outrank a niche personal-interest item.
- A dominant category is limited by diminishing utility without hard quotas or filler.
- English evidence becomes Chinese narrative on the LLM path while proper nouns can remain natural.
- Grounding validation can reliably enforce IDs and schema, but cannot prove every semantic claim without another model or external verifier; strict prompts, evidence-only payloads and offline fixtures provide the MVP control.
- No-key fallback remains readable but is not an offline translator and may retain English source wording.
