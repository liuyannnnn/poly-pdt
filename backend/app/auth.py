"""认证模块：三日轮换密码、邮件通知和本地 HttpOnly session。"""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from email.message import EmailMessage
import hashlib
import hmac
import random
import secrets
import smtplib
import ssl
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .config import Settings


PASSWORD_KEY = "auth:password:current"
HASH_ITERATIONS = 200_000


class PasswordNotifier(Protocol):
    async def send(self, to_address: str, subject: str, body: str) -> None: ...


class EmailNotifier:
    def __init__(self, settings: Settings):
        self._settings = settings

    async def send(self, to_address: str, subject: str, body: str) -> None:
        # smtplib 是同步库；这里发送频率极低，启动期阻塞一次可接受。
        if not (
            self._settings.smtp_host
            and self._settings.smtp_user
            and self._settings.smtp_password
            and self._settings.smtp_from
        ):
            raise RuntimeError("SMTP settings are incomplete")
        message = EmailMessage()
        message["From"] = self._settings.smtp_from
        message["To"] = to_address
        message["Subject"] = subject
        message.set_content(body)
        context = ssl.create_default_context()
        if self._settings.smtp_security.lower() == "ssl":
            with smtplib.SMTP_SSL(
                self._settings.smtp_host,
                self._settings.smtp_port,
                timeout=20,
                context=context,
            ) as smtp:
                smtp.login(self._settings.smtp_user, self._settings.smtp_password)
                smtp.send_message(message)
            return
        with smtplib.SMTP(self._settings.smtp_host, self._settings.smtp_port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(self._settings.smtp_user, self._settings.smtp_password)
            smtp.send_message(message)


class AuthManager:
    def __init__(
        self,
        *,
        store: Any,
        settings: Settings,
        notifier: PasswordNotifier | None = None,
        now: Callable[[], datetime] | None = None,
        password_factory: Callable[[], str] | None = None,
    ):
        self._store = store
        self._settings = settings
        self._notifier = notifier or EmailNotifier(settings)
        self._now = now or (lambda: datetime.now(UTC))
        self._password_factory = password_factory or self.generate_password

    @property
    def cookie_name(self) -> str:
        return self._settings.auth_cookie_name

    @property
    def session_ttl_seconds(self) -> int:
        return self._settings.auth_session_ttl_seconds or self._settings.auth_rotation_days * 24 * 60 * 60

    async def ensure_current_password_notified(self) -> dict[str, Any]:
        period = self._period()
        current = await self._store.get_json(PASSWORD_KEY)
        if current and current.get("period_id") == period["period_id"]:
            return {"generated": False, "period_id": period["period_id"], "expires_at_utc": current["expires_at_utc"]}

        password = self._password_factory()
        salt = secrets.token_hex(16)
        record = {
            "period_id": period["period_id"],
            "period_days": self._settings.auth_rotation_days,
            "salt": salt,
            "password_hash": _hash_password(password, salt),
            "created_at_utc": _iso_utc(self._now()),
            "expires_at_utc": period["expires_at_utc"],
            "sent_at_utc": None,
        }
        await self._store.set_json(PASSWORD_KEY, record, ttl_seconds=self.session_ttl_seconds + 24 * 60 * 60)
        if self._settings.auth_notify_channel == "email" and self._settings.auth_email_to:
            await self._notifier.send(
                self._settings.auth_email_to,
                self.email_subject(),
                self._email_body(password, period["expires_at_utc"]),
            )
            record["sent_at_utc"] = _iso_utc(self._now())
            await self._store.set_json(PASSWORD_KEY, record, ttl_seconds=self.session_ttl_seconds + 24 * 60 * 60)
        return {"generated": True, "period_id": period["period_id"], "expires_at_utc": period["expires_at_utc"]}

    async def login(self, password: str) -> dict[str, Any] | None:
        await self.ensure_current_password_notified()
        record = await self._store.get_json(PASSWORD_KEY)
        if not record or not _verify_password(password, record["salt"], record["password_hash"]):
            return None
        session_id = secrets.token_urlsafe(32)
        expires_at = datetime.fromtimestamp(self._now().timestamp() + self.session_ttl_seconds, UTC)
        await self._store.set_json(
            f"auth:session:{session_id}",
            {"created_at_utc": _iso_utc(self._now()), "expires_at_utc": _iso_utc(expires_at)},
            ttl_seconds=self.session_ttl_seconds,
        )
        return {"session_id": session_id, "expires_at_utc": _iso_utc(expires_at)}

    async def validate_session(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        return await self._store.get_json(f"auth:session:{session_id}") is not None

    async def logout(self, session_id: str | None) -> None:
        if session_id:
            await self._store.delete(f"auth:session:{session_id}")

    def session_payload(self, authenticated: bool) -> dict[str, Any]:
        period = self._period()
        return {
            "authenticated": authenticated,
            "rotation_days": self._settings.auth_rotation_days,
            "expires_at_utc": period["expires_at_utc"],
        }

    def _period(self) -> dict[str, str]:
        zone = ZoneInfo(self._settings.auth_timezone)
        local_date = self._now().astimezone(zone).date()
        days = max(1, self._settings.auth_rotation_days)
        start_ord = (local_date.toordinal() // days) * days
        start = date.fromordinal(start_ord)
        end = date.fromordinal(start_ord + days)
        expires_local = datetime.combine(end, time.min, tzinfo=zone)
        return {
            "period_id": f"{start.isoformat()}:{days}",
            "expires_at_utc": _iso_utc(expires_local),
        }

    def generate_password(self) -> str:
        # 邮件正文只放密码本身：前 4 位小写字母，后 4 位数字。
        rng = random.SystemRandom()
        letters = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(4))
        digits = "".join(rng.choice("0123456789") for _ in range(4))
        return f"{letters}{digits}"

    def email_subject(self) -> str:
        if self._settings.auth_email_subject:
            return self._settings.auth_email_subject
        local = self._now().astimezone(ZoneInfo(self._settings.auth_timezone))
        return f"PDT{local:%m%d}"

    def _email_body(self, password: str, expires_at_utc: str) -> str:
        return password


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), HASH_ITERATIONS)
    return base64.b64encode(digest).decode("ascii")


def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(_hash_password(password, salt), expected_hash)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
