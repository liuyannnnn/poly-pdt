"""外部连通性探针：只检查可达性，不返回密钥、token 或签名信息。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx
from websockets.legacy.client import connect as legacy_ws_connect

from .config import Settings, load_settings
from .pm_accounts import load_pm_account_configs


CHECKS = {
    "pm_http": "Polymarket HTTP",
    "pm_sports_ws": "Polymarket sports WS",
    "pm_market_ws": "Polymarket market WS",
    "pm_user_ws": "Polymarket user WS",
    "gs_http": "Goalserve HTTP",
    "gs_ws": "Goalserve WS",
}


class ConnectivityTransport(Protocol):
    async def check(self, key: str, url: str | None, timeout_seconds: float) -> tuple[bool, str]: ...


class HttpConnectivityTransport:
    async def check(self, key: str, url: str | None, timeout_seconds: float) -> tuple[bool, str]:
        if not url:
            return False, "not_configured"
        if url.startswith("ws://") or url.startswith("wss://"):
            return await self._check_ws(url, timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
                # PM Gamma 的裸 events URL 在部分网络下会误报；探针使用与 Collector 相同的足球查询。
                params = {"closed": "false", "limit": 1, "tag_slug": "soccer"} if key == "pm_http" else None
                response = await client.get(url, params=params)
            return response.status_code < 500, f"http_{response.status_code}"
        except Exception as exc:
            return False, exc.__class__.__name__

    async def _check_ws(self, url: str, timeout_seconds: float) -> tuple[bool, str]:
        try:
            async with asyncio.timeout(timeout_seconds):
                async with legacy_ws_connect(
                    url,
                    open_timeout=timeout_seconds,
                    close_timeout=min(timeout_seconds, 1.0),
                ):
                    return True, "ws_connected"
        except Exception as exc:
            return False, exc.__class__.__name__


class StaticConnectivityTransport:
    def __init__(self, results: dict[str, bool | tuple[bool, str]]):
        self._results = results

    async def check(self, key: str, url: str | None, timeout_seconds: float) -> tuple[bool, str]:
        if not url:
            return False, "not_configured"
        value = self._results.get(key, False)
        if isinstance(value, tuple):
            return value
        return bool(value), "ok" if value else "failed"


@dataclass
class ConnectivityChecker:
    settings: Settings | None = None
    transport: ConnectivityTransport | None = None

    async def check_all(self) -> dict[str, object]:
        settings = self.settings or load_settings()
        transport = self.transport or HttpConnectivityTransport()
        urls = {
            "pm_http": settings.pm_http_url,
            "pm_sports_ws": settings.pm_sports_ws_url,
            "pm_market_ws": settings.pm_market_ws_url,
            "pm_user_ws": settings.pm_user_ws_url if _pm_user_ws_configured(settings) else None,
            "gs_http": settings.gs_http_url,
            "gs_ws": settings.gs_ws_url,
        }
        checks = {}
        for key, label in CHECKS.items():
            ok, detail = await transport.check(key, urls.get(key), settings.connectivity_timeout_seconds)
            checks[key] = {
                "label": label,
                "ok": ok,
                "detail": detail,
                "configured": bool(urls.get(key)),
            }
        return {"checks": checks}


def _pm_user_ws_configured(settings: Settings) -> bool:
    if not settings.pm_user_ws_enabled or not settings.pm_user_ws_url:
        return False
    try:
        return any(account.has_api_credentials for account in load_pm_account_configs(settings))
    except ValueError:
        return False
