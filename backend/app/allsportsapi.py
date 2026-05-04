"""AllSportsAPI 接入：作为 GS 之外的独立足球外部数据源。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import json
import re
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from websockets.legacy.client import connect as legacy_ws_connect


ALLSPORTS_HTTP_URL = "https://apiv2.allsportsapi.com/football/"
ALLSPORTS_WS_URL = "wss://wss.allsportsapi.com/live_events"


class AllSportsAPIHttpClient:
    """ASA HTTP 足球客户端。

    Collector 只需要“昨今明赛程 + 当前 live 比赛”的标准化列表，
    后续由 PM guid 匹配决定是否写入 Redis。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = ALLSPORTS_HTTP_URL,
        timezone_name: str = "Asia/Shanghai",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 12.0,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._timezone_name = timezone_name
        self._timezone = ZoneInfo(timezone_name)
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout_seconds, follow_redirects=True)

    async def fetch_home(self) -> list[dict[str, Any]]:
        return await self._fetch({"met": "Livescore"})

    async def fetch_d1(self) -> list[dict[str, Any]]:
        today = datetime.now(self._timezone).date()
        return await self._fetch(
            {
                "met": "Fixtures",
                "from": (today - timedelta(days=1)).isoformat(),
                "to": (today + timedelta(days=1)).isoformat(),
            }
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _fetch(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        response = await self._client.get(
            self._base_url,
            params={**params, "APIkey": self._api_key, "timezone": self._timezone_name},
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) else payload
        rows = result if isinstance(result, list) else []
        return [normalize_allsportsapi_match(row, timezone_name=self._timezone_name) for row in rows if isinstance(row, dict)]


class AllSportsAPIWsSource:
    """ASA live socket 源；只输出 Listener 可处理的 asa_live payload。"""

    name = "asa_live"

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = ALLSPORTS_WS_URL,
        timezone_name: str = "Asia/Shanghai",
    ):
        self._api_key = api_key
        self._endpoint = endpoint
        self._timezone_name = timezone_name

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        url = _with_query(
            self._endpoint,
            {
                "APIkey": self._api_key,
                "timezone": self._timezone_name,
            },
        )
        async with legacy_ws_connect(url, ping_interval=20, ping_timeout=20, max_queue=2048) as websocket:
            yield {"__connection_status__": "connected"}
            async for raw in websocket:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="ignore")
                if isinstance(raw, str) and raw.lower() == "ping":
                    await websocket.send("pong")
                    continue
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                rows = payload if isinstance(payload, list) else [payload]
                for row in rows:
                    if isinstance(row, dict):
                        normalized = normalize_allsportsapi_ws_payload(row, timezone_name=self._timezone_name)
                        if normalized:
                            yield normalized


def normalize_allsportsapi_ws_payload(payload: dict[str, Any], *, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
    if payload.get("source") == "asa" and (
        payload.get("score_home") is not None
        or payload.get("score_away") is not None
        or payload.get("home_team")
        or payload.get("start_time_utc")
    ):
        return payload
    if payload.get("match_id") or payload.get("event_key"):
        return normalize_allsportsapi_match(payload, timezone_name=timezone_name)
    return {}


def normalize_allsportsapi_match(payload: dict[str, Any], *, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
    """把 ASA Fixtures/Livescore/WS 字段统一成外部比赛状态。"""

    event_key = _text(payload, "event_key", "event_id", "match_id", "id")
    home_score, away_score = _score(payload)
    match_time = _match_time(payload)
    status = _status_from_payload(payload, match_time)
    return {
        "source": "asa",
        "match_id": event_key,
        "pregame_id": event_key,
        "inplay_id": event_key,
        "league": _text(payload, "league_name", "league", "country_name") or "",
        "league_id": _text(payload, "league_key", "league_id") or "",
        "home_team": _text(payload, "event_home_team", "home_team", "home") or "",
        "away_team": _text(payload, "event_away_team", "away_team", "away") or "",
        "home_logo_url": _text(payload, "home_team_logo", "event_home_team_logo"),
        "away_logo_url": _text(payload, "away_team_logo", "event_away_team_logo"),
        "start_time_utc": _start_time(payload, timezone_name),
        "status": status,
        "score": {"home": home_score, "away": away_score},
        "score_home": home_score,
        "score_away": away_score,
        "match_time": match_time,
        "period": _period(status, match_time),
        "clock": match_time,
        "red_cards": _cards(payload, "red"),
        "yellow_cards": _cards(payload, "yellow"),
        "corners": _statistics_pair(payload, "Corners"),
        "shots_on_target": _statistics_pair(payload, "On Target"),
        "substitutions": payload.get("substitutes"),
        "var_events": _events_by_word(payload, "var"),
        "penalties": _events_by_word(payload, "penalty"),
        "free_kicks": _statistics_pair(payload, "Free Kicks"),
        "events": _events(payload),
        "lineups": payload.get("lineups"),
        "venue": _text(payload, "event_stadium", "event_stadium_name"),
        "ts": payload.get("ts") or payload.get("timestamp"),
        "raw": payload,
    }


def _with_query(endpoint: str, params: dict[str, str]) -> str:
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}{urlencode(params)}"


def _text(payload: Any, *keys: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _score(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    home = _int_or_none(payload.get("event_home_score") or payload.get("home_score"))
    away = _int_or_none(payload.get("event_away_score") or payload.get("away_score"))
    if home is not None or away is not None:
        return home, away
    for key in ("event_final_result", "event_ft_result", "event_halftime_result", "score"):
        value = payload.get(key)
        if value:
            matched = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(value))
            if matched:
                return int(matched.group(1)), int(matched.group(2))
    for row in reversed(_events(payload)):
        value = row.get("score") if isinstance(row, dict) else None
        if value:
            matched = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(value))
            if matched:
                return int(matched.group(1)), int(matched.group(2))
    return None, None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _start_time(payload: dict[str, Any], timezone_name: str) -> str:
    date_value = _text(payload, "event_date", "date")
    time_value = _text(payload, "event_time", "time")
    if not date_value or not time_value:
        return ""
    try:
        local_dt = datetime.fromisoformat(f"{date_value}T{time_value}").replace(tzinfo=ZoneInfo(timezone_name))
    except ValueError:
        return ""
    return local_dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _status_from_payload(payload: dict[str, Any], match_time: str) -> str | None:
    status = _canonical_status(payload) if _has_status_field(payload) else None
    if match_time and status in {None, "scheduled"}:
        return "live"
    return status


def _canonical_status(payload: dict[str, Any]) -> str:
    value = str(payload.get("event_status") or payload.get("status") or "").strip()
    live_flag = str(payload.get("event_live") or "").strip()
    normalized = value.lower().replace(" ", "").replace("-", "")
    if normalized in {"finished", "ft", "afterpen", "afterextra", "fulltime"}:
        return "finished"
    if normalized in {"halftime", "break", "ht"}:
        return "Break"
    if live_flag == "1" or re.match(r"^\d{1,3}(\+?\d*)?'?$", value):
        return "live"
    if normalized in {"postponed", "cancelled", "canceled", "suspended"}:
        return value or normalized
    return "scheduled"


def _has_status_field(payload: dict[str, Any]) -> bool:
    return any(key in payload and payload.get(key) not in {None, ""} for key in ("event_status", "status", "event_live"))


def _match_time(payload: dict[str, Any]) -> str:
    value = str(payload.get("event_status") or payload.get("match_time") or payload.get("clock") or "").strip()
    if re.match(r"^\d{1,3}(\+?\d*)?'?$", value):
        return value
    return "" if value.lower() in {"finished", "halftime", "not started"} else value


def _period(status: str, match_time: str) -> str:
    if status == "Break":
        return "HT"
    minute = _int_or_none(str(match_time).replace("'", ""))
    if minute is None:
        return ""
    return "2H" if minute > 45 else "1H"


def _cards(payload: dict[str, Any], color: str) -> dict[str, int] | None:
    cards = payload.get("cards")
    if not isinstance(cards, list):
        return None
    result = {"home": 0, "away": 0}
    for card in cards:
        if not isinstance(card, dict):
            continue
        card_type = str(card.get("card") or card.get("type") or "").lower()
        if color not in card_type:
            continue
        if card.get("home_fault") or card.get("home_player") or str(card.get("side") or "").lower() == "home":
            result["home"] += 1
        elif card.get("away_fault") or card.get("away_player") or str(card.get("side") or "").lower() == "away":
            result["away"] += 1
    return result


def _statistics_pair(payload: dict[str, Any], label: str) -> dict[str, int] | None:
    statistics = payload.get("statistics")
    if not isinstance(statistics, list):
        return None
    for row in statistics:
        if not isinstance(row, dict):
            continue
        name = str(row.get("type") or row.get("name") or "").lower()
        if label.lower() not in name:
            continue
        return {
            "home": _int_or_none(row.get("home")) or 0,
            "away": _int_or_none(row.get("away")) or 0,
        }
    return None


def _events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("goalscorers", "cards"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    return rows


def _events_by_word(payload: dict[str, Any], word: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in _events(payload):
        text = json.dumps(row, ensure_ascii=False).lower()
        if word in text:
            result.append(row)
    return result
