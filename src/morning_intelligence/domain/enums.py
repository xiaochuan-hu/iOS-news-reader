from enum import StrEnum


class SourceType(StrEnum):
    RSS = "rss"
    HACKERNEWS = "hackernews"


class PipelineStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
