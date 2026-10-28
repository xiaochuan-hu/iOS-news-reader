from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from morning_intelligence.config import Settings
from morning_intelligence.main import create_app
from morning_intelligence.storage import SQLiteRepository

INGEST_TOKEN = "private-email-ingest-token"


class IngestOnlyService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository
        self.refresh_calls = 0
        self.llm_calls = 0
        self.tts_calls = 0

    async def deadline_daily_refresh(self, local_date) -> None:
        del local_date
        self.refresh_calls += 1
        raise AssertionError("ingestion must not trigger refresh")


def _client(tmp_path, *, token: str = INGEST_TOKEN):
    repository = SQLiteRepository(tmp_path / "ingest.db")
    repository.initialize()
    service = IngestOnlyService(repository)
    settings = Settings(
        _env_file=None,
        morning_database_path=repository.path,
        morning_email_ingest_token=token,
    )
    client = TestClient(create_app(settings=settings, service=service))  # type: ignore[arg-type]
    return client, service, repository


def _payload(message_id: str = "gmail-1", *, subject: str = "COMP0123 room update"):
    return {
        "messages": [
            {
                "message_id": message_id,
                "thread_id": "thread-1",
                "received_at": "2026-10-28T05:04:00Z",
                "sender": "UCL <ucl@example.test>",
                "recipients": ["student@example.test"],
                "cc": [],
                "subject": subject,
                "snippet": "The lecture room has changed.",
                "body_text": "The lecture will be in room 101.",
            }
        ]
    }


def test_email_ingest_rejects_missing_wrong_and_unconfigured_token(tmp_path) -> None:
    client, _, _ = _client(tmp_path / "configured")
    assert client.post("/v1/email-ingest", json=_payload()).status_code == 401
    response = client.post(
        "/v1/email-ingest",
        headers={"Authorization": "Bearer wrong"},
        json=_payload(),
    )
    assert response.status_code == 401
    assert "MORNING_EMAIL_INGEST_TOKEN" not in response.text

    disabled, _, _ = _client(tmp_path / "disabled", token="")
    assert disabled.post(
        "/v1/email-ingest",
        headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
        json=_payload(),
    ).status_code == 401


def test_valid_ingest_is_idempotent_and_does_not_trigger_generation(tmp_path) -> None:
    client, service, repository = _client(tmp_path)
    headers = {"Authorization": f"Bearer {INGEST_TOKEN}"}

    first = client.post("/v1/email-ingest", headers=headers, json=_payload())
    repeated = client.post("/v1/email-ingest", headers=headers, json=_payload())

    assert first.status_code == 200 and first.json() == {"accepted": 1}
    assert repeated.status_code == 200 and repeated.json() == {"accepted": 1}
    messages = repository.get_email_messages(
        datetime(2026, 10, 28, 0, tzinfo=UTC),
        datetime(2026, 10, 29, 0, tzinfo=UTC),
    )
    assert len(messages) == 1
    assert service.refresh_calls == service.llm_calls == service.tts_calls == 0


def test_ingest_filters_generated_briefs_and_enforces_request_limit(tmp_path) -> None:
    client, _, repository = _client(tmp_path)
    headers = {"Authorization": f"Bearer {INGEST_TOKEN}"}
    feedback = client.post(
        "/v1/email-ingest",
        headers=headers,
        json=_payload(subject="UCL Morning Brief | 2026-10-28"),
    )
    assert feedback.status_code == 200 and feedback.json() == {"accepted": 0}

    oversized = {"messages": [_payload(f"id-{index}")["messages"][0] for index in range(101)]}
    response = client.post("/v1/email-ingest", headers=headers, json=oversized)
    assert response.status_code == 422
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0] == 0


def test_ingest_requires_timezone_aware_received_at(tmp_path) -> None:
    client, _, _ = _client(tmp_path)
    payload = _payload()
    payload["messages"][0]["received_at"] = "2026-10-28T05:04:00"

    response = client.post(
        "/v1/email-ingest",
        headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
        json=payload,
    )
    assert response.status_code == 422
