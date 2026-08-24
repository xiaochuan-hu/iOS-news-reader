from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RSSFeedConfig(BaseModel):
    name: str
    url: HttpUrl
    category: str = "world"
    quality: float = Field(default=0.6, ge=0, le=1)
    enabled: bool = True


class HackerNewsConfig(BaseModel):
    enabled: bool = True
    fetch_top_stories: int = Field(default=30, ge=1, le=100)
    min_score: int = Field(default=80, ge=0)
    category: str = "technology"
    quality: float = Field(default=0.7, ge=0, le=1)


class SourcesConfig(BaseModel):
    rss: list[RSSFeedConfig] = Field(default_factory=list)
    hackernews: HackerNewsConfig = Field(default_factory=HackerNewsConfig)


class WeightedPreference(BaseModel):
    weight: float = Field(ge=0, le=3)


class BriefingConfig(BaseModel):
    # Retained as API/config compatibility hints only. V5 never clips content to them.
    target_minutes: int = Field(default=5, ge=1, le=10)
    min_events: int = Field(default=5, ge=1, le=30)
    max_events: int = Field(default=10, ge=1, le=30)
    min_event_score: float = Field(default=0.2, ge=0, le=1)
    characters_per_minute: int = Field(default=260, ge=180, le=360)

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_max_events_override(cls, value: Any) -> Any:
        if isinstance(value, dict) and "max_events" in value and "min_events" not in value:
            value = dict(value)
            value["min_events"] = min(5, int(value["max_events"]))
        return value

    @model_validator(mode="after")
    def event_range_is_valid(self) -> BriefingConfig:
        if self.min_events > self.max_events:
            raise ValueError("briefing min_events cannot exceed max_events")
        return self


class EditorialConfig(BaseModel):
    minimum_score: float = Field(default=0.44, ge=0, le=1)
    global_weight: float = Field(default=0.75, ge=0, le=1)
    personal_weight: float = Field(default=0.25, ge=0, le=1)
    diversity_bonus: float = Field(default=0.08, ge=0, le=0.5)
    category_overflow_penalty: float = Field(default=0.22, ge=0, le=0.5)
    ai_top_items: int = Field(default=5, ge=1, le=10)
    general_top_items: int = Field(default=5, ge=0, le=10)
    ai_research_minimum_impact: float = Field(default=0.65, ge=0, le=1)
    category_targets: dict[str, int] = Field(
        default_factory=lambda: {
            "world": 2,
            "ai_llm_agent": 3,
            "technology": 2,
            "finance": 1,
            "uk": 1,
            "china": 1,
        }
    )

    @model_validator(mode="after")
    def editorial_weights_sum_to_one(self) -> EditorialConfig:
        if abs(self.global_weight + self.personal_weight - 1.0) > 0.0001:
            raise ValueError("editorial global_weight and personal_weight must sum to 1.0")
        return self


class RankingWeights(BaseModel):
    recency: float = 0.25
    source_quality: float = 0.20
    cross_source_confirmation: float = 0.20
    personal_interest: float = 0.20
    novelty: float = 0.10
    llm_importance: float = 0.05

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> RankingWeights:
        if abs(sum(self.model_dump().values()) - 1.0) > 0.0001:
            raise ValueError("ranking weights must sum to 1.0")
        return self


class ProcessingConfig(BaseModel):
    fuzzy_title_threshold: int = Field(default=76, ge=40, le=100)
    lookback_hours: int = Field(default=24, ge=1, le=168)
    novelty_lookback_days: int = Field(default=3, ge=1, le=30)


class PreferencesConfig(BaseModel):
    language: str = "zh-CN"
    briefing: BriefingConfig = Field(default_factory=BriefingConfig)
    interests: dict[str, WeightedPreference] = Field(default_factory=dict)
    avoid: dict[str, WeightedPreference] = Field(default_factory=dict)
    editorial: EditorialConfig = Field(default_factory=EditorialConfig)
    ranking: RankingWeights = Field(default_factory=RankingWeights)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    morning_api_token: str = ""
    morning_database_path: Path = Path("data/morning_intelligence.db")
    morning_sources_config: Path = Path("config/sources.yaml")
    morning_preferences_config: Path = Path("config/preferences.yaml")
    morning_source_timeout_seconds: float = Field(default=8, gt=0)
    morning_auxiliary_timeout_seconds: float = Field(default=8, gt=0)
    morning_llm_timeout_seconds: float = Field(default=60, gt=0)
    morning_generation_deadline_seconds: float = Field(default=90, gt=0)
    morning_generation_trigger: Literal["client", "server"] = "client"
    morning_background_refresh_enabled: bool = False
    morning_manual_generation_enabled: bool = False
    morning_min_llm_interval_hours: float = Field(default=20, ge=20, le=168)
    morning_refresh_timezone: str = "Europe/London"
    morning_refresh_time_local: str = "06:00"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-5-mini"
    llm_thinking_mode: Literal["enabled", "disabled"] | None = None

    @field_validator("morning_refresh_timezone")
    @classmethod
    def timezone_must_be_iana_name(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("must be a valid IANA timezone") from exc
        return value

    @field_validator("morning_refresh_time_local")
    @classmethod
    def refresh_time_must_be_hh_mm(cls, value: str) -> str:
        parts = value.split(":")
        if (
            len(parts) != 2
            or len(parts[0]) != 2
            or len(parts[1]) != 2
            or not all(part.isascii() and part.isdigit() for part in parts)
        ):
            raise ValueError("must use strict HH:MM format")
        hour, minute = (int(part) for part in parts)
        if hour > 23 or minute > 59:
            raise ValueError("must be a valid local time")
        return value

    def load_sources(self) -> SourcesConfig:
        return SourcesConfig.model_validate(_load_yaml(self.morning_sources_config))

    def load_preferences(self) -> PreferencesConfig:
        return PreferencesConfig.model_validate(_load_yaml(self.morning_preferences_config))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"configuration file does not exist: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"configuration root must be a mapping: {path}")
    return data


@lru_cache
def get_settings() -> Settings:
    return Settings()
