"""GGScore football score feed helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from difflib import SequenceMatcher
import json
import re
from typing import Any
import unicodedata
from urllib.parse import urlencode

import httpx
from websockets.legacy.client import connect as legacy_ws_connect


GGS_FOOTBALL_RESULT_URL = "https://api.esportsdata.cc/sport/api/v3/live/202/result"
GGS_MATCH_LIST_URL = "https://api.esportsdata.cc/sport/api/v3/live/list/match"
GGS_WS_URL = "wss://ws.esportsdata.cc/sport/ws/v3/live"
GGS_FOOTBALL_SPORT_ID = "202"


class GGScoreHttpClient:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        base_url: str = GGS_FOOTBALL_RESULT_URL,
        match_list_url: str = GGS_MATCH_LIST_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ):
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url
        self._match_list_url = match_list_url
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout_seconds, follow_redirects=True)

    async def fetch_result(self, match_id: str | None = None) -> list[dict[str, Any]]:
        params = {"match_id": match_id} if match_id else {}
        response = await self._client.get(
            self._base_url,
            params=params,
            headers={"app_id": self._app_id, "app_secret": self._app_secret},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else payload
        rows = data.get("list") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        return [row for row in (normalize_ggscore_match(item) for item in rows if isinstance(item, dict)) if row]

    async def fetch_match_list(
        self,
        *,
        match_id: str | None = None,
        start_time_from: int | None = None,
        start_time_to: int | None = None,
        sport_id: str = GGS_FOOTBALL_SPORT_ID,
        language: str = "en",
        per_page: int = 100,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "sport_id": sport_id,
            "language": language,
            "per_page": per_page,
        }
        if match_id:
            params["match_id"] = match_id
        if start_time_from is not None:
            params["start_time_from"] = int(start_time_from)
        if start_time_to is not None:
            params["start_time_to"] = int(start_time_to)
        normalized: list[dict[str, Any]] = []
        for page in range(1, max(1, max_pages) + 1):
            response = await self._client.get(
                self._match_list_url,
                params={**params, "page": page},
                headers={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else payload
            rows = data.get("list") if isinstance(data, dict) else data
            if not isinstance(rows, list) or not rows:
                break
            normalized.extend(
                row
                for row in (normalize_ggscore_match_info(item) for item in rows if isinstance(item, dict))
                if row
            )
            total = int(data.get("total") or 0) if isinstance(data, dict) else 0
            if match_id or page * per_page >= total:
                break
        return normalized

    async def close(self) -> None:
        await self._client.aclose()


class GGScoreHttpPollSource:
    name = "ggs_http_poll"

    def __init__(self, *, store: Any, client: GGScoreHttpClient, interval_seconds: float = 2.0):
        self._store = store
        self._client = client
        self._interval_seconds = interval_seconds

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        import asyncio

        yield {"__connection_status__": "connected"}
        while True:
            for match_id in await self._bound_match_ids():
                for row in await self._client.fetch_result(match_id):
                    row["received_at_utc"] = _utc_now()
                    yield row
            await asyncio.sleep(self._interval_seconds)

    async def _bound_match_ids(self) -> list[str]:
        prefix = "idx:ggs:id:"
        return [key.removeprefix(prefix) for key in await self._store.keys(f"{prefix}*")]


class GGScoreWsSource:
    name = "ggs_live"

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        endpoint: str = GGS_WS_URL,
        sport_id: str = GGS_FOOTBALL_SPORT_ID,
    ):
        self._app_id = app_id
        self._app_secret = app_secret
        self._endpoint = endpoint
        self._sport_id = sport_id

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        url = _with_query(
            self._endpoint,
            {"app_id": self._app_id, "app_secret": self._app_secret, "sport_id": self._sport_id},
        )
        async with legacy_ws_connect(url, ping_interval=20, ping_timeout=20, max_queue=2048) as websocket:
            yield {"__connection_status__": "connected"}
            async for raw in websocket:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="ignore")
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                received_at = _utc_now()
                for row in normalize_ggscore_ws_payload(payload):
                    row["received_at_utc"] = received_at
                    yield row


def normalize_ggscore_ws_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    provider_ts = _ts_from_millis(payload.get("push_time_millis"))
    data = payload.get("data", payload)
    rows = data if isinstance(data, list) else [data]
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = normalize_ggscore_match(row, provider_ts_utc=provider_ts)
        if item:
            normalized.append(item)
    return normalized


def normalize_ggscore_match(payload: dict[str, Any], *, provider_ts_utc: str | None = None) -> dict[str, Any]:
    match_id = _text(payload, "match_id", "id")
    if not match_id:
        return {}
    team_fields = _team_fields(payload.get("teams"))
    provider_ts = provider_ts_utc or _ts_from_seconds(_first_present(payload, "update_time", "updated_at"))
    return {
        "source": "ggs",
        "match_id": match_id,
        "inplay_id": match_id,
        "score": {"home": team_fields["score_home"], "away": team_fields["score_away"]},
        "score_home": team_fields["score_home"],
        "score_away": team_fields["score_away"],
        "home_team": team_fields["home_team"],
        "away_team": team_fields["away_team"],
        "home_team_id": team_fields["home_team_id"],
        "away_team_id": team_fields["away_team_id"],
        "status": payload.get("status"),
        "match_time": str(payload.get("match_time") or payload.get("clock") or ""),
        "period": str(payload.get("period") or ""),
        "clock": str(payload.get("clock") or payload.get("match_time") or ""),
        "provider_ts_utc": provider_ts,
        "ts": provider_ts,
        "raw": payload,
    }


def normalize_ggscore_match_info(payload: dict[str, Any]) -> dict[str, Any]:
    match_id = _text(payload, "match_id", "id")
    if not match_id:
        return {}
    team_fields = _team_fields(payload.get("teams"))
    league = payload.get("league") if isinstance(payload.get("league"), dict) else {}
    provider_ts = _ts_from_seconds(_first_present(payload, "update_time", "updated_at"))
    return {
        "source": "ggs",
        "match_id": match_id,
        "inplay_id": match_id,
        "external_match_id": match_id,
        "league": _text(league, "name_en", "name", "abbr_en", "abbr"),
        "home_team": team_fields["home_team"],
        "away_team": team_fields["away_team"],
        "home_team_id": team_fields["home_team_id"],
        "away_team_id": team_fields["away_team_id"],
        "score": {"home": team_fields["score_home"], "away": team_fields["score_away"]},
        "score_home": team_fields["score_home"],
        "score_away": team_fields["score_away"],
        "start_time_utc": _ts_from_seconds(payload.get("start_time")),
        "status": payload.get("status"),
        "provider_ts_utc": provider_ts,
        "ts": provider_ts,
        "raw": payload,
    }


def rank_ggscore_matches_for_pm(pm: dict[str, Any], rows: list[dict[str, Any]], *, limit: int = 50) -> list[dict[str, Any]]:
    pm_start = _parse_ts(pm.get("start_time_utc"))
    candidates: list[dict[str, Any]] = []
    for row in rows:
        row_start = _parse_ts(row.get("start_time_utc"))
        if pm_start is None or row_start is None:
            time_delta = 0
            time_score = 0.0
        else:
            time_delta = abs(pm_start - row_start)
            time_score = max(0.0, 1.0 - time_delta / (12 * 60 * 60))
        home = _team_similarity(str(pm.get("home_team") or ""), str(row.get("home_team") or ""))
        away = _team_similarity(str(pm.get("away_team") or ""), str(row.get("away_team") or ""))
        flipped_home = _team_similarity(str(pm.get("home_team") or ""), str(row.get("away_team") or ""))
        flipped_away = _team_similarity(str(pm.get("away_team") or ""), str(row.get("home_team") or ""))
        team_score = max((home + away) / 2, (flipped_home + flipped_away) / 2)
        confidence = round(team_score * 0.9 + time_score * 0.1, 4)
        if time_delta > 12 * 60 * 60 or team_score < 0.72:
            continue
        candidate = {
            **row,
            "source": "ggs",
            "external_match_id": str(row.get("external_match_id") or row.get("match_id") or ""),
            "league": row.get("league") or "",
            "match_time": row.get("match_time") or "",
            "confidence": confidence,
            "team_confidence": round(team_score, 4),
            "time_delta_seconds": int(time_delta),
        }
        candidates.append(candidate)
    candidates.sort(key=lambda candidate: (-float(candidate["confidence"]), int(candidate["time_delta_seconds"])))
    return candidates[: max(1, int(limit))]


def best_ggscore_match_for_pm(pm: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = rank_ggscore_matches_for_pm(pm, rows, limit=1)
    return candidates[0] if candidates else None


def _team_fields(teams: Any) -> dict[str, Any]:
    fields = {
        "score_home": None,
        "score_away": None,
        "home_team": None,
        "away_team": None,
        "home_team_id": None,
        "away_team_id": None,
    }
    for team in teams if isinstance(teams, list) else []:
        if not isinstance(team, dict):
            continue
        side = str(team.get("side") or team.get("position") or "").strip()
        if side == "1":
            fields["score_home"] = _score_value(team.get("score"))
            fields["home_team"] = _team_name(team) or _team_id(team)
            fields["home_team_id"] = _team_id(team)
        elif side == "2":
            fields["score_away"] = _score_value(team.get("score"))
            fields["away_team"] = _team_name(team) or _team_id(team)
            fields["away_team_id"] = _team_id(team)
    return fields


def _team_name(team: dict[str, Any]) -> str | None:
    nested = team.get("team") if isinstance(team.get("team"), dict) else {}
    team_info = team.get("team_info") if isinstance(team.get("team_info"), dict) else {}
    return _text(
        team,
        "team_name",
        "name",
        "full_name",
        "short_name",
        "display_name",
        "team_name_en",
        "name_en",
    ) or _text(
        team_info,
        "team_name",
        "name_en",
        "name",
        "full_name",
        "short_name",
        "display_name",
        "abbr_en",
        "abbr",
    ) or _text(
        nested,
        "team_name",
        "name",
        "full_name",
        "short_name",
        "display_name",
        "team_name_en",
        "name_en",
    )


def _team_id(team: dict[str, Any]) -> str | None:
    nested = team.get("team") if isinstance(team.get("team"), dict) else {}
    team_info = team.get("team_info") if isinstance(team.get("team_info"), dict) else {}
    return _text(team, "team_id", "id", "team_key", "team_uid") or _text(
        team_info,
        "team_id",
        "id",
        "team_key",
        "team_uid",
    ) or _text(
        nested,
        "team_id",
        "id",
        "team_key",
        "team_uid",
    )


def _score_value(value: Any) -> int | None:
    if isinstance(value, list):
        return _int_or_none(value[0] if value else None)
    return _int_or_none(value)


def _with_query(endpoint: str, params: dict[str, str]) -> str:
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}{urlencode(params)}"


def _text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) is not None:
            return payload.get(key)
    return None


def _ts_from_seconds(value: Any) -> str | None:
    parsed = _int_or_none(value)
    if parsed is None:
        return None
    if parsed > 10_000_000_000:
        parsed = parsed // 1000
    return datetime.fromtimestamp(parsed, UTC).isoformat().replace("+00:00", "Z")


def _parse_ts(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        parsed = _int_or_none(value)
        return float(parsed) if parsed is not None else None


def _team_similarity(left: str, right: str) -> float:
    left_value = _normalize_team(left)
    right_value = _normalize_team(right)
    if not left_value or not right_value:
        return 0.0
    left_tokens = set(left_value.split())
    right_tokens = set(right_value.split())
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return max(SequenceMatcher(None, left_value, right_value).ratio(), overlap)


def _normalize_team(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"\b(fc|cf|sc|afc|club|football|soccer)\b", " ", text)
    text = text.replace("munchen", "munich")
    text = text.replace("st germain", "saint germain")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _ts_from_millis(value: Any) -> str | None:
    parsed = _int_or_none(value)
    if parsed is None:
        return None
    return datetime.fromtimestamp(parsed / 1000, UTC).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
