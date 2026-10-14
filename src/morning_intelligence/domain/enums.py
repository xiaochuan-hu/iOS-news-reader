from enum import StrEnum


class SourceType(StrEnum):
    RSS = "rss"
    HACKERNEWS = "hackernews"


class PipelineStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class GitHubTrendingSource(StrEnum):
    LIVE_FULL = "live_full"
    LIVE_PARTIAL = "live_partial"
    CACHED_FALLBACK = "cached_fallback"
    UNAVAILABLE = "unavailable"
