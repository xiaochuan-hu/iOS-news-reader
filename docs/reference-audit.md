# Reference audit

Audit date: 2026-08-17. Repositories were downloaded as read-only source archives under `/private/tmp/morning-intelligence-references`; none was added to this project's Git history. The SHA values below are the `main` branch heads reported by the GitHub API on the audit date.

## Horizon — primary news-engine reference

- Repository: `Thysrael/Horizon`
- URL: https://github.com/Thysrael/Horizon
- HEAD: `5064bb92f40ef8c667d5b1264cea60fa8db3dcbf`
- License: MIT (`LICENSE`)
- Inspected: README; `docs/configuration.md`, `docs/scoring.md`, `docs/scrapers.md`; source models, orchestrator, RSS and Hacker News scrapers, AI analysis/enrichment/dedup prompts, webhook delivery; representative dedup, balancing, source, enrichment, storage and webhook tests; example configuration, profiles/prompts, `.env.example`, `pyproject.toml`, Docker files.
- Architecture: source-specific `BaseScraper.fetch(since)` adapters produce one strict `ContentItem`; sources run concurrently; exact URL merge precedes profile-based analysis, thresholding, optional AI topic dedup, category quotas, enrichment, summary and delivery.
- Useful patterns: one shared async HTTP client; per-source failure outcomes; conservative URL identity that removes known tracking parameters but preserves meaningful query order; deepest-content winner on exact duplicates; batch topic dedup with failure passthrough; threshold and category balance before enrichment; environment-variable names rather than secrets in runtime config.
- Reuse: concepts and independently written equivalents for adapter protocol, source outcome reporting, conservative canonical URLs, bounded concurrency, shortlist-before-LLM, category balancing and best-effort source collection.
- Do not reuse: Horizon classes or prompts verbatim, its one-LLM-call-per-item scoring path, large provider/source surface, Markdown/webhook/email/MCP delivery stack, or its item-level model as our event layer. Morning Intelligence needs deterministic scoring, explicit `NewsItem != NewsEvent`, SQLite novelty history and a latency-first HTTP API.

## OpenJarvis — harness and service reference

- Repository: `open-jarvis/OpenJarvis`
- URL: https://github.com/open-jarvis/OpenJarvis
- HEAD: `1ca3ec6ceab69125b273b2be191f3536431f26da`
- License: Apache-2.0 (`LICENSE`)
- Inspected: README; morning-digest and scheduler guides; minimal/macOS/Linux presets; `MorningDigestAgent`, digest store and API routes; scheduler/store and TTS abstractions; digest, route, scheduling and TTS tests; configuration and trace-store material.
- Architecture: a thin morning-digest orchestrator delegates collection, one narrative synthesis, optional evaluation, TTS and artifact storage. Scheduled, on-demand and continuous work are separate roles. Digests and task/run state use SQLite. TTS is behind an abstract backend.
- Useful patterns: precomputed digest artifact; cached-vs-fresh CLI/API semantics; scheduler isolated from the agent; SQLite WAL for digest storage; explicit configured sections; prompt states that collected data is the only factual evidence; evaluator failure never blocks delivery.
- Reuse: independently implemented service boundaries (`CollectorService -> EventProcessor -> RankingService -> BriefingAgent -> BriefingStore -> API`), precomputed artifacts, startup background refresh, run records and graceful evaluator/LLM degradation.
- Do not reuse: OpenJarvis as a dependency, its general agent/tool registry, connectors, local-model runtime, desktop frontend, server TTS or broad scheduler framework. They exceed the MVP and its digest fetches at generation time rather than optimizing our alarm-stop cache path.

## shortcuts-api — iOS automation reference

- Repository: `gavinsawyer/shortcuts-api`
- URL: https://github.com/gavinsawyer/shortcuts-api
- HEAD: `da70b3a77c766771373f2356caa27d5c9eae4833`
- License: `package.json` declares MIT; no standalone `LICENSE` was present in the audited tree.
- Inspected: README; `shortcuts/Automation Triggers/On Stop Wake-Up Alarm.shortcut`; example image/docs; core config/API shortcut files; Firebase callable API source and project configuration. No repository test suite was present.
- Architecture: a Personal Automation uses `Alarm Is Stopped: Wake-Up` to invoke an imported trigger shortcut, which performs remote state/API work and then device/home actions.
- Reuse: trigger semantics only: `Alarm -> Is Stopped -> Wake-Up -> Run Shortcut -> HTTP`.
- Do not reuse: shortcut file, Firebase/Firestore implementation, Focus/HomeKit/location state, callable payload or credentials. The absent standalone license and unrelated scope both argue for a clean-room manual setup guide.

## AwesomeMinis — voice-first UX reference

- Repository: `OpenMinis/AwesomeMinis`
- URL: https://github.com/OpenMinis/AwesomeMinis
- HEAD: `d30439250406ec86d76548b0ccd7f36e89339be2`
- License: CC0-1.0 (`LICENSE`)
- Inspected: README; English and Chinese `X Timeline Voice Alarm`, `Smart Daily Briefing`, and iOS scheduled-task cases; compatibility notes. This is a use-case catalog, not an application test suite.
- Architecture: iOS automation triggers a remote task; data is collected and summarized into a briefing; optional TTS generates/plays audio. The cases explicitly warn that iOS background shell loops are unreliable.
- Reuse: voice-first interaction, fully automatic trigger, compact prompt/payload and testing while locked.
- Do not reuse: Minis ecosystem, X login/cookies, Doubao TTS, scheduled-time trigger or server-generated audio. Native iOS Speak Text is a smaller and safer MVP.

## news-broadcast-system — spoken-content reference

- Repository: `chianwu-hash/news-broadcast-system`
- URL: https://github.com/chianwu-hash/news-broadcast-system
- HEAD: `f4341b9b6f36ba28fb7ba0c1924428cecc7cd387`
- License: none found in the audited repository. Treat all implementation and prompts as view-only.
- Inspected: README, `AGENTS.md`, `CLAUDE.md`, `PROJECT_HANDOVER.md`, project status; shared selection/script/TTS/normalization scripts; morning runner; history/config files. There is no conventional automated test directory.
- Architecture: shell pipeline `search -> filter/select -> write spoken script -> normalize for TTS -> edge-tts -> mix -> deliver`, with program-specific runners and shared utilities. History prevents replaying old stories.
- Useful patterns: a spoken script is a distinct artifact; explicit factual-grounding and no-invented-number rules; target character budget; pronounceable numbers; pre-TTS cleanup; persisted history for novelty.
- Reuse: ideas only—separate spoken pass, natural short sentences, TTS-safe cleanup, length checks and novelty history.
- Do not reuse: any code or prompt text because no license is supplied; no server TTS, BGM, Telegram, Brave search, host-specific shell paths or homophone substitution in the MVP.

## AI-News-Briefing — research and evaluation reference

- Repository: `hoangsonww/AI-News-Briefing`
- URL: https://github.com/hoangsonww/AI-News-Briefing
- HEAD: `c9896362092500a4e31dddcb0a44f50cd7d24f5d`
- License: MIT (`LICENSE`)
- Inspected: README and architecture; daily/custom prompts; Bash entry points and engine fallback; eval README, rubric, judge prompt, schema, runner, drift logic, golden fixtures and harness tests.
- Architecture: an OS scheduler runs a headless research prompt through an available CLI; daily research is published as artifacts. Custom briefs fan out five research angles. The eval harness extracts output, scores five dimensions, upserts by version/model/date, compares a golden set and detects robust median/MAD drift.
- Useful patterns: real-date verification, omit-rather-than-guess, covered-story history, artifact-based success checks, deterministic offline judge, versioned/idempotent eval records, golden regression and drift monitoring.
- Reuse: a small fixture-based evaluator covering freshness, deduplication, importance, coverage, grounding, naturalness, length and latency; failure must be visible but not block cached delivery.
- Do not reuse: five-agent daily research, Notion/Teams/Slack/Obsidian publishing, AI CLI orchestration or multi-minute web research. That belongs in a future `deep_dive` mode, not the wake path.

## Audit conclusion

The implementation is clean-room. Permissive references allow reuse, but copying would import mismatched abstractions. The MVP will combine Horizon's staged noise reduction, OpenJarvis's thin service/artifact boundary, native Shortcuts trigger semantics, and spoken/eval constraints learned from the remaining projects.
