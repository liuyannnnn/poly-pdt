from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthManager
from app.config import Settings
from app.main import create_app
from app.store import MemoryStore


@dataclass
class CapturedEmail:
    to_address: str
    subject: str
    body: str


class CapturingNotifier:
    def __init__(self):
        self.messages: list[CapturedEmail] = []

    async def send(self, to_address: str, subject: str, body: str) -> None:
        self.messages.append(CapturedEmail(to_address, subject, body))


def extract_password(body: str) -> str:
    return body.strip()


@pytest.mark.asyncio
async def test_auth_manager_generates_three_day_password_and_sends_once_without_plaintext_storage():
    store = MemoryStore()
    notifier = CapturingNotifier()
    settings = Settings(
        auth_enabled=True,
        auth_rotation_days=3,
        auth_email_to="user@example.com",
        auth_notify_channel="email",
    )
    now = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    manager = AuthManager(
        store=store,
        settings=settings,
        notifier=notifier,
        now=lambda: now,
        password_factory=lambda: "pw-0501",
    )

    first = await manager.ensure_current_password_notified()
    second = await manager.ensure_current_password_notified()
    stored = await store.get_json("auth:password:current")

    assert first["generated"] is True
    assert second["generated"] is False
    assert len(notifier.messages) == 1
    assert notifier.messages[0].to_address == "user@example.com"
    assert notifier.messages[0].subject == "PDT0501"
    assert notifier.messages[0].body == "pw-0501"
    assert "pw-0501" not in str(stored)
    assert stored["period_days"] == 3


@pytest.mark.asyncio
async def test_auth_manager_rotates_password_after_three_day_period():
    store = MemoryStore()
    notifier = CapturingNotifier()
    settings = Settings(
        auth_enabled=True,
        auth_rotation_days=3,
        auth_email_to="user@example.com",
        auth_notify_channel="email",
    )
    current = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    passwords = iter(["pw-a", "pw-b"])
    manager = AuthManager(
        store=store,
        settings=settings,
        notifier=notifier,
        now=lambda: current,
        password_factory=lambda: next(passwords),
    )

    await manager.ensure_current_password_notified()
    current = current + timedelta(days=3, minutes=1)
    await manager.ensure_current_password_notified()

    assert [extract_password(message.body) for message in notifier.messages] == ["pw-a", "pw-b"]


def test_auth_endpoints_protect_api_and_login_with_http_only_cookie():
    store = MemoryStore()
    notifier = CapturingNotifier()
    settings = Settings(
        auth_enabled=True,
        auth_rotation_days=3,
        auth_email_to="user@example.com",
        auth_notify_channel="email",
    )
    auth_manager = AuthManager(
        store=store,
        settings=settings,
        notifier=notifier,
        now=lambda: datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
        password_factory=lambda: "pw-0501",
    )
    app = create_app(store=store, settings=settings, auth_manager=auth_manager)

    with TestClient(app) as client:
        assert client.get("/api/v1/matches").status_code == 401
        assert client.get("/api/v1/auth/session").json()["authenticated"] is False
        bad_login = client.post("/api/v1/auth/login", json={"password": "bad"})
        assert bad_login.status_code == 401

        login = client.post("/api/v1/auth/login", json={"password": "pw-0501"})
        assert login.status_code == 200
        assert login.json()["authenticated"] is True
        assert "HttpOnly" in login.headers["set-cookie"]
        assert client.get("/api/v1/matches").status_code == 200

        logout = client.post("/api/v1/auth/logout")
        assert logout.status_code == 200
        assert client.get("/api/v1/matches").status_code == 401


def test_generated_password_is_four_lowercase_letters_plus_four_digits_and_subject_uses_month_day():
    store = MemoryStore()
    settings = Settings(auth_enabled=True, auth_rotation_days=3, auth_email_to="user@example.com")
    manager = AuthManager(
        store=store,
        settings=settings,
        now=lambda: datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
    )

    password = manager.generate_password()
    subject = manager.email_subject()

    assert re.fullmatch(r"[a-z]{4}\d{4}", password)
    assert subject == "PDT0501"
