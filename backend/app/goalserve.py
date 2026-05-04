"""Goalserve 接入：HTTP 使用 JSON feed，WS 只提供可选 source，不默认连接。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from websockets.legacy.client import connect as legacy_ws_connect


GOALSERVE_HOME_URL = "http://livescore.goalserve.com/api/v1/soccer/home"
GOALSERVE_D1_URL_TEMPLATE = "https://www.goalserve.com/getfeed/{api_key}/soccernew/d1"
GOALSERVE_TOKEN_URL = "http://live.goalserve.com/api/v1/auth/gettoken"
GOALSERVE_WS_URL_TEMPLATE = "ws://live.goalserve.com/ws/{sport}?tkn={token}"


def resolve_goalserve_api_key(feeds_file: str | None) -> str | None:
    if not feeds_file:
        return None
    path = Path(feeds_file)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    matched = re.search(r"getfeed/([a-fA-F0-9]{32})/", text)
    if matched is not None:
        return matched.group(1)
    matched = re.search(r"apiKey=([a-fA-F0-9]{32})", text)
    return matched.group(1) if matched is not None else None


class GoalserveHttpClient:
    def __init__(
        self,
        *,
        api_key: str,
        home_url: str = GOALSERVE_HOME_URL,
        d1_url_template: str = GOALSERVE_D1_URL_TEMPLATE,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ):
        self._api_key = api_key
        self._home_url = home_url
        self._d1_url_template = d1_url_template
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout_seconds, follow_redirects=True)

    async def fetch_home(self) -> list[dict[str, Any]]:
        return await self._fetch(self._home_url)

    async def fetch_d1(self) -> list[dict[str, Any]]:
        return await self._fetch(self._d1_url_template.format(api_key=self._api_key))

    async def close(self) -> None:
        await self._client.aclose()

    async def _fetch(self, url: str) -> list[dict[str, Any]]:
        response = await self._client.get(_with_goalserve_json_query(url, self._api_key))
        response.raise_for_status()
        return parse_goalserve_feed(response.json())


class GoalserveWsSource:
    name = "gs_live"

    def __init__(
        self,
        *,
        api_key: str,
        sport: str = "soccer",
        token_url: str = GOALSERVE_TOKEN_URL,
        ws_url_template: str = GOALSERVE_WS_URL_TEMPLATE,
        timeout_seconds: float = 20.0,
    ):
        self._api_key = api_key
        self._sport = re.sub(r"[^a-z0-9_-]+", "", sport.lower()) or "soccer"
        self._token_url = token_url
        self._ws_url_template = ws_url_template
        self._timeout_seconds = timeout_seconds

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        token = await self._fetch_token()
        if not token:
            return
        url = self._ws_url_template.format(sport=self._sport, token=token)
        async with legacy_ws_connect(url, ping_interval=20, ping_timeout=20, max_queue=2048) as websocket:
            yield {"__connection_status__": "connected"}
            async for message in websocket:
                if isinstance(message, bytes):
                    message = message.decode("utf-8", errors="ignore")
                if message == "ping":
                    await websocket.send("pong")
                    continue
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                rows = payload if isinstance(payload, list) else [payload]
                for row in rows:
                    if isinstance(row, dict):
                        normalized = normalize_goalserve_ws_payload(row)
                        if normalized:
                            yield normalized

    async def _fetch_token(self) -> str:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(self._token_url, json={"apiKey": self._api_key})
            response.raise_for_status()
            payload = response.json()
        return str(payload.get("token") or "").strip()


def normalize_goalserve_ws_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # Goalserve WS 原始字段较分散，这里统一转成 Listener 已识别的 gs_live payload。
    match_id = _text(payload, "static_id", "@static_id", "staticId") or _text(payload, "id", "@id", "mid", "match_id")
    inplay_id = _text(payload, "id", "@id", "mid", "match_id")
    score_home, score_away = _extract_score(payload)
    normalized = {
        "match_id": match_id,
        "inplay_id": inplay_id,
        "home_team": _team_name(payload.get("t1") or payload.get("team1") or payload.get("localteam")),
        "away_team": _team_name(payload.get("t2") or payload.get("team2") or payload.get("visitorteam")),
        "status": _text(payload, "status", "@status", "s", "state"),
        "match_time": _text(payload, "match_time", "clock", "min", "m", "status", "@status") or "",
        "period": _text(payload, "period", "p") or "",
        "clock": _text(payload, "clock", "min", "m", "status", "@status") or "",
        "score": {"home": score_home, "away": score_away},
        "red_cards": _stat_pair(payload, "rc", "red_cards", "redcards"),
        "yellow_cards": _stat_pair(payload, "yc", "yellow_cards", "yellowcards"),
        "corners": _stat_pair(payload, "corners", "corner", "c"),
        "shots_on_target": _stat_pair(payload, "shots_on_target", "sot"),
        "events": _extract_events(payload),
        "substitutions": _events_by_type(payload, "substitution"),
        "var_events": _events_by_type(payload, "var"),
        "penalties": _events_by_type(payload, "penalty"),
        "free_kicks": _events_by_type(payload, "free_kick"),
        "raw": payload,
    }
    return normalized


def parse_goalserve_feed(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for category, match in _iter_goalserve_matches(payload):
        rows.append(_normalize_goalserve_match(category, match))
    return rows


def _normalize_goalserve_match(category: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    static_id = _text(match, "static_id", "@static_id", "staticId")
    live_id = _text(match, "id", "@id", "mid", "match_id")
    match_id = static_id or live_id
    score_home, score_away = _extract_score(match)
    status = _text(match, "status", "@status", "time", "@time") or ""
    events = _extract_events(match)
    return {
        "match_id": match_id,
        "pregame_id": static_id or match_id,
        "inplay_id": live_id,
        "static_id": static_id,
        "league": _text(category, "name", "@name") or _text(match, "league", "@league") or "",
        "league_id": _text(category, "id", "@id", "gid", "@gid") or "",
        "home_team": _team_name(match.get("localteam") or match.get("t1") or match.get("team1")),
        "away_team": _team_name(match.get("visitorteam") or match.get("t2") or match.get("team2")),
        "start_time_utc": _start_time(match),
        "status": status,
        "score": {"home": score_home, "away": score_away},
        "match_time": status,
        "period": _text(match, "period", "@period") or "",
        "clock": _text(match, "clock", "@clock", "match_time", "@status", "status") or status,
        "red_cards": _stat_pair(match, "rc", "red_cards", "redcards"),
        "yellow_cards": _stat_pair(match, "yc", "yellow_cards", "yellowcards"),
        "corners": _stat_pair(match, "corners", "corner", "c"),
        "shots_on_target": _stat_pair(match, "shots_on_target", "sot"),
        "substitutions": _events_by_type(match, "substitution"),
        "var_events": _events_by_type(match, "var"),
        "penalties": _events_by_type(match, "penalty"),
        "free_kicks": _events_by_type(match, "free_kick"),
        "events": events,
        "lineups": _extract_lineups(match),
        "commentary_available": _text(match, "commentary_available", "@commentary_available") or "",
        "venue": _text(match.get("venue"), "name", "@name") or "",
        "odds": _extract_odds(match),
        "raw": match,
    }


def _iter_goalserve_matches(payload: Any) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    root = payload.get("scores", payload) if isinstance(payload, dict) else payload
    categories = _as_list(root.get("category")) if isinstance(root, dict) else []
    if not categories:
        categories = [{}]
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for category in categories:
        if not isinstance(category, dict):
            continue
        candidates = [
            category.get("matches", {}),
            category.get("match"),
            category,
        ]
        for candidate in candidates:
            for match in _match_dicts(candidate):
                rows.append((category, match))
    return rows


def _match_dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows: list[dict[str, Any]] = []
        for item in value:
            rows.extend(_match_dicts(item))
        return rows
    if not isinstance(value, dict):
        return []
    if "match" in value:
        return _match_dicts(value["match"])
    if any(key in value for key in ("localteam", "visitorteam", "t1", "t2", "team1", "team2")):
        return [value]
    return []


def _extract_score(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    stat = payload.get("stat") or payload.get("stats") or {}
    if isinstance(stat, dict):
        pair = _parse_pair(stat.get("a") or stat.get("@a"))
        if pair != (None, None):
            return pair
    for key in ("score", "@score", "sc", "@sc"):
        pair = _parse_pair(payload.get(key))
        if pair != (None, None):
            return pair
    home = _int_from(_team_attr(payload.get("localteam") or payload.get("t1") or payload.get("team1"), "goals", "g"))
    away = _int_from(_team_attr(payload.get("visitorteam") or payload.get("t2") or payload.get("team2"), "goals", "g"))
    return home, away


def _stat_pair(payload: dict[str, Any], *keys: str) -> dict[str, int | None]:
    stat = payload.get("stat") or payload.get("stats") or {}
    if not isinstance(stat, dict):
        stat = {}
    for key in keys:
        pair = _parse_pair(stat.get(key) or stat.get(f"@{key}") or payload.get(key) or payload.get(f"@{key}"))
        if pair != (None, None):
            return {"home": pair[0], "away": pair[1]}
    return {"home": None, "away": None}


def _extract_events(payload: dict[str, Any]) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    for item in _event_items(payload):
        event_type = _event_type(_text(item, "type", "@type", "event", "name", "@name"))
        if event_type is None:
            continue
        result = _normalize_score_text(_text(item, "result", "@result"))
        rows.append(
            {
                "type": event_type,
                "team": _team_side(_text(item, "team", "@team", "side", "tm")),
                "player": _text(item, "player", "@player", "p", "name", "@name"),
                "minute": _text(item, "minute", "@minute", "min", "m"),
                "result": result,
            }
        )
    return rows


def _events_by_type(payload: dict[str, Any], expected_type: str) -> list[dict[str, str | None]]:
    return [event for event in _extract_events(payload) if event["type"] == expected_type]


def _event_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = []
    for key in ("events", "event", "incidents", "incident", "cards"):
        values.extend(_nested_items(payload.get(key), "event"))
    return [item for item in values if isinstance(item, dict)]


def _event_type(value: str | None) -> str | None:
    text = str(value or "").lower()
    if not text:
        return None
    if "yellow" in text:
        return "yellow_card"
    if "red" in text:
        return "red_card"
    if "sub" in text:
        return "substitution"
    if "var" in text:
        return "var"
    if "pen" in text:
        return "penalty"
    if "free" in text:
        return "free_kick"
    if "goal" in text:
        return "goal"
    return text.replace(" ", "_")


def _extract_lineups(match: dict[str, Any]) -> dict[str, list[str]]:
    lineups = match.get("lineups") or match.get("lineup") or {}
    if not isinstance(lineups, dict):
        return {"home": [], "away": []}
    return {
        "home": _players(lineups.get("localteam") or lineups.get("home") or {}),
        "away": _players(lineups.get("visitorteam") or lineups.get("away") or {}),
    }


def _players(value: Any) -> list[str]:
    rows = _nested_items(value, "player")
    return [name for item in rows if isinstance(item, dict) for name in [_text(item, "name", "@name")] if name]


def _extract_odds(match: dict[str, Any]) -> dict[str, Any]:
    odds = match.get("odds") or {}
    if isinstance(odds, dict):
        return odds
    return {}


def _with_goalserve_json_query(url: str, api_key: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("json", "1")
    if "livescore.goalserve.com" in parts.netloc:
        query.setdefault("apiKey", api_key)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _start_time(match: dict[str, Any]) -> str:
    value = _text(match, "start_time", "start_time_utc")
    if value:
        return value
    date = _text(match, "formatted_date", "@formatted_date", "date", "@date")
    time = _text(match, "time", "@time")
    if not date or not time:
        return ""
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(f"{date} {time}", fmt).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return ""


def _parse_pair(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, dict):
        return _int_from(value.get("home") or value.get("h")), _int_from(value.get("away") or value.get("a"))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _int_from(value[0]), _int_from(value[1])
    text = str(value or "").strip()
    matched = re.search(r"(\d+)\s*[-:]\s*(\d+)", text)
    if matched is None:
        return None, None
    return int(matched.group(1)), int(matched.group(2))


def _normalize_score_text(value: str | None) -> str | None:
    if not value:
        return None
    matched = re.search(r"(\d+)\s*[-:]\s*(\d+)", value)
    return f"{matched.group(1)}-{matched.group(2)}" if matched is not None else value


def _team_side(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"localteam", "home", "t1", "1"}:
        return "home"
    if text in {"visitorteam", "away", "t2", "2"}:
        return "away"
    return text or None


def _team_name(value: Any) -> str:
    return _team_attr(value, "name", "n") or ""


def _team_attr(value: Any, *keys: str) -> str | None:
    if not isinstance(value, dict):
        return None
    return _text(value, *keys, *(f"@{key}" for key in keys))


def _text(value: Any, *keys: str) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        item = value.get(key)
        if item is None:
            continue
        text = str(item).strip()
        if text:
            return text
    return None


def _int_from(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nested_items(value: Any, nested_key: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        nested = value.get(nested_key)
        if nested is not None:
            return _as_list(nested)
        return [value]
    return []


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]
