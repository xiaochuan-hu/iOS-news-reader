from __future__ import annotations

import pytest
from pydantic import ValidationError

from morning_intelligence.config import Settings


def test_default_daily_refresh_configuration() -> None:
    settings = Settings(_env_file=None)

    assert settings.morning_refresh_timezone == "Europe/London"
    assert settings.morning_refresh_time_local == "06:00"
    assert settings.morning_generation_trigger == "client"
    assert settings.morning_background_refresh_enabled is False
    assert settings.morning_manual_generation_enabled is False
    assert settings.morning_min_llm_interval_hours == 20
    assert settings.morning_llm_timeout_seconds == 60
    assert settings.morning_generation_deadline_seconds == 180
    assert settings.morning_auxiliary_timeout_seconds == 8
    assert settings.morning_tts_enabled is False
    assert settings.morning_tts_provider == "azure"
    assert settings.morning_tts_voice == "zh-CN-YunxiNeural"
    assert settings.morning_tts_style == "newscast"
    assert settings.morning_tts_rate == "0%"
    assert settings.morning_tts_timeout_seconds == 45
    assert settings.morning_audio_retention_days == 7
    assert settings.morning_media_url_ttl_seconds == 14_400
    assert settings.morning_podcast_token == ""


@pytest.mark.parametrize("timezone", ["Europe/London", "Asia/Singapore", "UTC"])
def test_valid_iana_timezone(timezone: str) -> None:
    settings = Settings(_env_file=None, morning_refresh_timezone=timezone)
    assert settings.morning_refresh_timezone == timezone


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        Settings(_env_file=None, morning_refresh_timezone="Europe/Not-A-Place")


@pytest.mark.parametrize("local_time", ["00:00", "06:00", "23:59"])
def test_valid_strict_local_refresh_time(local_time: str) -> None:
    assert (
        Settings(_env_file=None, morning_refresh_time_local=local_time).morning_refresh_time_local
        == local_time
    )


@pytest.mark.parametrize(
    "local_time",
    ["6:00", "06:0", "06:00:00", "24:00", "12:60", "noon", "０６:００"],
)
def test_invalid_local_refresh_time_is_rejected(local_time: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, morning_refresh_time_local=local_time)


@pytest.mark.parametrize("mode", ["enabled", "disabled", None])
def test_thinking_mode_parsing(mode: str | None) -> None:
    assert Settings(_env_file=None, llm_thinking_mode=mode).llm_thinking_mode == mode  # type: ignore[arg-type]


def test_invalid_thinking_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_thinking_mode="automatic")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("azure_speech_region", "https://not-a-region"),
        ("morning_tts_voice", "voice<unsafe>"),
        ("morning_tts_style", "style with spaces"),
        ("morning_tts_rate", "fast"),
    ],
)
def test_invalid_tts_ssml_configuration_is_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_media_signing_key_requires_adequate_length_when_configured() -> None:
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        Settings(_env_file=None, morning_media_signing_key="too-short")

    key = "k" * 32
    assert Settings(_env_file=None, morning_media_signing_key=key).morning_media_signing_key == key


def test_podcast_token_requires_adequate_length_when_configured() -> None:
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        Settings(_env_file=None, morning_podcast_token="too-short")

    token = "p" * 32
    assert Settings(_env_file=None, morning_podcast_token=token).morning_podcast_token == token
