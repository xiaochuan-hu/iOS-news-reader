from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from morning_intelligence.config import Settings
from morning_intelligence.domain import Briefing
from morning_intelligence.main import create_app


class FakeService:
    def __init__(self) -> None:
        self.briefing = Briefing(
            generated_at=datetime.now(UTC),
            event_count=1,
            estimated_duration_seconds=42,
            spoken_text="早上好。这是一段可以直接朗读的晨报。",
            sources=["Fixture News"],
        )
        self.generate_args = None

    async def latest_or_generate(self) -> Briefing:
        return self.briefing

    async def generate(self, **kwargs) -> Briefing:
        self.generate_args = kwargs
        return self.briefing


def make_client(token: str = "test-token") -> tuple[TestClient, FakeService]:
    settings = Settings(
        morning_api_token=token,
        morning_background_refresh_enabled=False,
    )
    service = FakeService()
    return TestClient(create_app(settings=settings, service=service)), service  # type: ignore[arg-type]


def test_health_is_public() -> None:
    client, _ = make_client()
    assert client.get("/health").json() == {"status": "ok"}


def test_briefing_requires_auth() -> None:
    client, _ = make_client()
    response = client.get("/v1/briefings/latest")
    assert response.status_code == 401
    assert "MORNING_API_TOKEN" not in response.text


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


def test_generate_contract_passes_low_latency_options() -> None:
    client, service = make_client()
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
