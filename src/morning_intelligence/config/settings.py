from __future__ import annotations

import re
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
    morning_email_ingest_token: str = ""
    morning_database_path: Path = Path("data/morning_intelligence.db")
    morning_sources_config: Path = Path("config/sources.yaml")
    morning_preferences_config: Path = Path("config/preferences.yaml")
    morning_source_timeout_seconds: float = Field(default=8, gt=0)
    morning_auxiliary_timeout_seconds: float = Field(default=8, gt=0)
    morning_llm_timeout_seconds: float = Field(default=60, gt=0)
    morning_generation_deadline_seconds: float = Field(default=180, gt=0)
    morning_generation_trigger: Literal["client", "server"] = "client"
    morning_background_refresh_enabled: bool = False
    morning_manual_generation_enabled: bool = False
    morning_min_llm_interval_hours: float = Field(default=20, ge=20, le=168)
    morning_refresh_timezone: str = "Europe/London"
    morning_refresh_time_local: str = "06:00"
    morning_tts_enabled: bool = False
    morning_tts_provider: Literal["azure"] = "azure"
    morning_tts_voice: str = "zh-CN-YunxiNeural"
    morning_tts_style: str = "newscast"
    morning_tts_rate: str = "0%"
    morning_tts_timeout_seconds: float = Field(default=45, gt=0)
    morning_audio_directory: Path = Path("/app/data/audio")
    morning_audio_retention_days: int = Field(default=7, ge=1)
    morning_media_signing_key: str = ""
    morning_media_url_ttl_seconds: int = Field(default=14_400, ge=300, le=86_400)
    morning_podcast_token: str = ""
    azure_speech_key: str = ""
    azure_speech_region: str = ""
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

    @field_validator("azure_speech_region")
    @classmethod
    def azure_region_must_be_safe(cls, value: str) -> str:
        if value and not re.fullmatch(r"[a-z0-9-]+", value):
            raise ValueError("must contain only lowercase letters, numbers, and hyphens")
        return value

    @field_validator("morning_tts_voice", "morning_tts_style")
    @classmethod
    def azure_ssml_names_must_be_safe(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9-]+", value):
            raise ValueError("must contain only letters, numbers, and hyphens")
        return value

    @field_validator("morning_tts_rate")
    @classmethod
    def azure_rate_must_be_percentage(cls, value: str) -> str:
        if not re.fullmatch(r"[+-]?\d{1,3}%", value):
            raise ValueError("must be a percentage such as 0%, +10%, or -10%")
        return value

    @field_validator("morning_media_signing_key", "morning_podcast_token")
    @classmethod
    def private_tokens_must_be_strong_when_set(cls, value: str) -> str:
        if value and len(value.encode("utf-8")) < 32:
            raise ValueError("must be at least 32 bytes when configured")
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
