from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, HttpUrl, model_validator
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
    target_minutes: int = Field(default=5, ge=1, le=10)
    max_events: int = Field(default=10, ge=1, le=30)
    min_event_score: float = Field(default=0.2, ge=0, le=1)


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
    lookback_hours: int = Field(default=30, ge=1, le=168)
    novelty_lookback_days: int = Field(default=3, ge=1, le=30)


class PreferencesConfig(BaseModel):
    language: str = "zh-CN"
    briefing: BriefingConfig = Field(default_factory=BriefingConfig)
    interests: dict[str, WeightedPreference] = Field(default_factory=dict)
    avoid: dict[str, WeightedPreference] = Field(default_factory=dict)
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
    morning_refresh_interval_minutes: int = Field(default=20, ge=1)
    morning_source_timeout_seconds: float = Field(default=8, gt=0)
    morning_llm_timeout_seconds: float = Field(default=20, gt=0)
    morning_generation_deadline_seconds: float = Field(default=25, gt=0)
    morning_background_refresh_enabled: bool = True
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-5-mini"

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
