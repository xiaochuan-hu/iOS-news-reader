from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from morning_intelligence.config import Settings
from morning_intelligence.domain import Briefing
from morning_intelligence.main import create_app
from morning_intelligence.services.morning_briefing import DailyRefreshResult


class FakeService:
    def __init__(self, *, cached: bool = True) -> None:
        self.briefing = (
            Briefing(
                generated_at=datetime.now(UTC),
                event_count=1,
                estimated_duration_seconds=42,
                spoken_text="早上好。这是一段可以直接朗读的晨报。",
                sources=["Fixture News"],
            )
            if cached
            else None
        )
        self.generate_args = None
        self.latest_calls = 0
        self.refresh_calls = 0

    def latest(self) -> Briefing | None:
        self.latest_calls += 1
        return self.briefing

    async def deadline_daily_refresh(self, local_date: date) -> DailyRefreshResult:
        self.refresh_calls += 1
        return DailyRefreshResult("generated", local_date, self.briefing)

    async def generate(self, **kwargs) -> Briefing:
        self.generate_args = kwargs
        assert self.briefing is not None
        return self.briefing


def make_client(
    token: str = "test-token",
    *,
    cached: bool = True,
    manual_enabled: bool = False,
    background_enabled: bool = False,
) -> tuple[TestClient, FakeService]:
    settings = Settings(
        _env_file=None,
        morning_api_token=token,
        morning_manual_generation_enabled=manual_enabled,
        morning_background_refresh_enabled=background_enabled,
    )
    service = FakeService(cached=cached)
    return TestClient(create_app(settings=settings, service=service)), service  # type: ignore[arg-type]


def test_health_is_public() -> None:
    client, _ = make_client()
    assert client.get("/health").json() == {"status": "ok"}


def test_briefing_requires_auth() -> None:
    client, _ = make_client()
    response = client.get("/v1/briefings/latest")
    assert response.status_code == 401
    assert "MORNING_API_TOKEN" not in response.text


def test_briefing_rejects_wrong_bearer_token() -> None:
    client, _ = make_client()
    response = client.get(
        "/v1/briefings/latest/text",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_latest_json_and_plain_text_contracts() -> None:
    client, _ = make_client()
    headers = {"Authorization": "Bearer test-token"}
    response = client.get("/v1/briefings/latest", headers=headers)
    assert response.status_code == 200
    assert response.json()["event_count"] == 1
    assert "freshness_minutes" in response.json()

    text = client.get("/v1/briefings/latest/text?token=test-token")
    assert text.status_code == 200
    assert text.text.startswith("早上好")
    assert text.headers["content-type"].startswith("text/plain; charset=utf-8")


def test_get_endpoints_are_cache_only_across_100_reads_each() -> None:
    client, service = make_client()
    headers = {"Authorization": "Bearer test-token"}
    expected = service.briefing.spoken_text if service.briefing else ""

    for _ in range(100):
        assert client.get("/v1/briefings/latest", headers=headers).status_code == 200
        response = client.get("/v1/briefings/latest/text", headers=headers)
        assert response.status_code == 200
        assert response.text == expected

    assert service.refresh_calls == 0
    assert service.generate_args is None
    assert service.latest_calls == 200


def test_empty_cache_gets_return_503_without_generation() -> None:
    client, service = make_client(cached=False)
    headers = {"Authorization": "Bearer test-token"}

    assert client.get("/v1/briefings/latest", headers=headers).status_code == 503
    assert client.get("/v1/briefings/latest/text", headers=headers).status_code == 503
    assert service.refresh_calls == 0


def test_daily_refresh_contract_is_idempotent_friendly() -> None:
    client, service = make_client()
    today = datetime.now(UTC).date()
    response = client.post(
        "/v1/briefings/daily-refresh",
        headers={"Authorization": "Bearer test-token"},
        json={"local_date": today.isoformat()},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "generated"
    assert response.json()["local_date"] == today.isoformat()
    assert response.json()["briefing_id"] == service.briefing.id  # type: ignore[union-attr]
    assert service.refresh_calls == 1


def test_daily_refresh_validates_strict_format_and_utc_window() -> None:
    client, service = make_client()
    headers = {"Authorization": "Bearer test-token"}
    today = datetime.now(UTC).date()

    for value in ("2026/08/19", "Aug 19 2026", "2026-8-19"):
        assert client.post(
            "/v1/briefings/daily-refresh",
            headers=headers,
            json={"local_date": value},
        ).status_code == 422
    for value in (today - timedelta(days=2), today + timedelta(days=2)):
        assert client.post(
            "/v1/briefings/daily-refresh",
            headers=headers,
            json={"local_date": value.isoformat()},
        ).status_code == 422

    assert service.refresh_calls == 0


def test_daily_refresh_accepts_utc_date_plus_or_minus_one() -> None:
    client, service = make_client()
    headers = {"Authorization": "Bearer test-token"}
    today = datetime.now(UTC).date()

    for value in (today - timedelta(days=1), today, today + timedelta(days=1)):
        response = client.post(
            "/v1/briefings/daily-refresh",
            headers=headers,
            json={"local_date": value.isoformat()},
        )
        assert response.status_code == 200

    assert service.refresh_calls == 3


def test_manual_generate_is_disabled_by_default() -> None:
    client, service = make_client()
    response = client.post(
        "/v1/briefings/generate",
        headers={"Authorization": "Bearer test-token"},
        json={"mode": "wake"},
    )
    assert response.status_code == 409
    assert service.generate_args is None


def test_manual_generate_enabled_still_uses_safe_service_entry() -> None:
    client, service = make_client(manual_enabled=True)
    response = client.post(
        "/v1/briefings/generate",
        headers={"Authorization": "Bearer test-token"},
        json={
            "mode": "wake",
            "target_minutes": 4,
            "incremental_refresh": True,
            "max_age_minutes": 20,
        },
    )
    assert response.status_code == 200
    assert service.generate_args == {
        "target_minutes": 4,
        "incremental_refresh": True,
        "max_age_minutes": 20,
    }


def test_startup_never_generates_even_if_old_background_flag_is_true() -> None:
    client, service = make_client(cached=False, background_enabled=True)
    with client:
        assert client.get("/health").status_code == 200
    assert service.refresh_calls == 0
