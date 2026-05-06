"""Collector：负责 PM/GS HTTP 采集、PM 主导匹配和 Redis 当前态写入。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from .models import CollectorStatus
from .timeseries import MATCH_RELATED_TTL_SECONDS, append_pm_collector_snapshot


# PM/外部源 当前态和索引保留 3 天，避免跨比赛日的旧映射污染实时消息。
MATCH_STATE_TTL_SECONDS = MATCH_RELATED_TTL_SECONDS


class PMClient(Protocol):
    async def fetch_events(self) -> list[dict[str, Any]]: ...


class GSClient(Protocol):
    async def fetch_home(self) -> list[dict[str, Any]]: ...

    async def fetch_d1(self) -> list[dict[str, Any]]: ...


class StaticPMHttpClient:
    def __init__(self, events: list[dict[str, Any]] | None = None):
        self._events = events or []

    async def fetch_events(self) -> list[dict[str, Any]]:
        return list(self._events)


class StaticGSHttpClient:
    def __init__(
        self,
        home: list[dict[str, Any]] | None = None,
        d1: list[dict[str, Any]] | None = None,
    ):
        self._home = home or []
        self._d1 = d1 or []

    async def fetch_home(self) -> list[dict[str, Any]]:
        return list(self._home)

    async def fetch_d1(self) -> list[dict[str, Any]]:
        return list(self._d1)


@dataclass(frozen=True)
class MatchCandidate:
    gs: dict[str, Any]
    confidence: float
    time_delta_seconds: int = 0


class Collector:
    def __init__(
        self,
        store: Any | None = None,
        pm_client: PMClient | None = None,
        gs_client: GSClient | None = None,
        asa_client: GSClient | None = None,
        broadcaster: Any | None = None,
        trader_manager: Any | None = None,
        interval_seconds: float = 5 * 60,
        now: Callable[[], datetime | str] | None = None,
        timezone_name: str = "Asia/Shanghai",
    ):
        self._store = store
        self._pm_client = pm_client or StaticPMHttpClient()
        self._gs_client = gs_client or StaticGSHttpClient()
        self._asa_client = asa_client or StaticGSHttpClient()
        self._broadcaster = broadcaster
        self._trader_manager = trader_manager
        self._now = now or (lambda: datetime.now(UTC))
        self._timezone = ZoneInfo(timezone_name)
        self._last_report: dict[str, Any] | None = None
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._collect_lock: asyncio.Lock | None = None
        self._wakeup: asyncio.Event | None = None
        self._interval_seconds = interval_seconds
        self._last_error: str | None = None
        self._next_run_at: str | None = None
        self._football_volume_threshold_k = 0
        self._upcoming_days: int | None = None
        self._external_source = "gs"

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._collect_lock = asyncio.Lock()
        self._wakeup = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        task = self._task
        self._running = False
        self._task = None
        self._wakeup = None
        self._collect_lock = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def set_interval_minutes(self, minutes: int) -> None:
        self._interval_seconds = minutes * 60
        if self._running and self._wakeup is not None:
            self._wakeup.set()

    def set_filters(self, *, football_volume_threshold_k: int, upcoming_days: int | None = 2) -> None:
        self._football_volume_threshold_k = max(0, int(football_volume_threshold_k))
        self._upcoming_days = None if upcoming_days is None else max(1, int(upcoming_days))

    def set_external_source(self, source: str) -> None:
        self._external_source = source if source in {"gs", "asa", "none"} else "none"

    def _lock(self) -> asyncio.Lock:
        if self._collect_lock is None:
            self._collect_lock = asyncio.Lock()
        return self._collect_lock

    def status(self) -> CollectorStatus:
        matches_count = len(self._last_report["bindings"]) if self._last_report else 0
        return CollectorStatus(
            collector_running=self._running,
            collector_last_run_at=self._last_report.get("completed_at_utc") if self._last_report else None,
            collector_last_success_at=self._last_report.get("completed_at_utc") if self._last_report else None,
            collector_last_error=self._last_error,
            collector_next_run_at=self._next_run_at,
            matches_count=matches_count,
        )

    async def collect_once(self) -> dict[str, Any]:
        if self._store is None:
            raise RuntimeError("Collector requires a store")

        async with self._lock():
            # Collector 以 PM 为基准：先写 PM，再尝试绑定当前外部源；没有外部源时保留 pending。
            now_dt = self._now_datetime()
            completed_at = _format_utc(now_dt)
            raw_pm = [row for row in await self._pm_client.fetch_events() if _sport(row) == "football"]
            raw_gs: list[dict[str, Any]] = []
            raw_asa: list[dict[str, Any]] = []
            if self._external_source == "gs":
                raw_gs = [
                    *[row for row in await self._gs_client.fetch_home() if _sport(row) == "football"],
                    *[row for row in await self._gs_client.fetch_d1() if _sport(row) == "football"],
                ]
            elif self._external_source == "asa":
                raw_asa = [
                    *[row for row in await self._asa_client.fetch_home() if _sport(row) == "football"],
                    *[row for row in await self._asa_client.fetch_d1() if _sport(row) == "football"],
                ]
            pm_matches = [
                pm
                for pm in (_parse_pm_match(row, completed_at) for row in raw_pm)
                if self._passes_pm_filter(pm, now_dt)
            ]
            seen_pm_event_ids = {pm["pm_event_id"] for pm in pm_matches}
            for cached_pm in await self._cached_unbound_pm_matches(now_dt):
                if cached_pm.get("pm_event_id") not in seen_pm_event_ids:
                    pm_matches.append(cached_pm)
                    seen_pm_event_ids.add(cached_pm["pm_event_id"])
            gs_matches = [_parse_gs_match(row, completed_at) for row in raw_gs]
            asa_matches = [_parse_asa_match(row, completed_at) for row in raw_asa]
            external_matches = gs_matches if self._external_source == "gs" else asa_matches

            bindings: list[dict[str, Any]] = []
            pending_bindings: list[dict[str, Any]] = []
            used_external_ids: set[str] = set()

            for pm in pm_matches:
                guid = _guid_for(pm["pm_event_id"])
                candidate = _match_gs(pm, external_matches, used_external_ids)
                await self._write_pm(guid, pm)
                snapshot = await append_pm_collector_snapshot(self._store, guid, pm, completed_at)
                await self._write_pm_indices(guid, pm)

                if candidate is None:
                    binding = _binding(guid, pm, None, 0.0, "pending", completed_at)
                    pending_bindings.append(binding)
                else:
                    gs = dict(candidate.gs, guid=guid)
                    used_external_ids.add(_external_match_id(gs))
                    binding = _binding(guid, pm, gs, candidate.confidence, "matched", completed_at)
                    if gs.get("source") == "asa":
                        await self._write_asa(guid, gs)
                        await self._write_asa_indices(guid, gs)
                    else:
                        await self._write_gs(guid, gs)
                        await self._write_gs_indices(guid, gs)
                    bindings.append(binding)

                await self._store.set_json(f"binding:{guid}", binding, ttl_seconds=MATCH_STATE_TTL_SECONDS)
                await self._store.set_text(f"idx:match:status:{binding['status']}:{guid}", guid, ttl_seconds=MATCH_STATE_TTL_SECONDS)
                await self._store.set_text(f"idx:guid:{guid}", guid, ttl_seconds=MATCH_STATE_TTL_SECONDS)
                await self._publish_pm_snapshot(guid, pm, snapshot, binding)

            report = {
                "completed_at_utc": completed_at,
                "external_source": self._external_source,
                "pm_seen": len(pm_matches),
                "gs_seen": len(gs_matches),
                "asa_seen": len(asa_matches),
                "matched": len(bindings),
                "pending": len(pending_bindings),
                "bindings": bindings,
                "pending_bindings": pending_bindings,
            }
            self._last_report = report
            self._last_error = None
            await self._store.set_json("collector:last_report", report, ttl_seconds=MATCH_STATE_TTL_SECONDS)
            return report

    def _now_datetime(self) -> datetime:
        value = self._now()
        if isinstance(value, str):
            parsed = _parse_datetime(value)
            if parsed is not None:
                return parsed
            return datetime.now(UTC)
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _passes_pm_filter(self, pm: dict[str, Any], now_dt: datetime) -> bool:
        # 未开赛比赛只看今明两天；已经开赛但跨过本地零点的比赛仍要继续采集。
        if _float_value(pm.get("total_volume")) < self._football_volume_threshold_k * 1000:
            return False
        if _is_finished_status(pm.get("status")) and _finished_for_more_than(pm, now_dt, minutes=15):
            return False
        if self._upcoming_days is None:
            return True
        start = _parse_datetime(str(pm.get("start_time_utc") or ""))
        if start is None:
            return False
        today = now_dt.astimezone(self._timezone).date()
        match_date = start.astimezone(self._timezone).date()
        if today <= match_date < today + timedelta(days=self._upcoming_days):
            return True
        return _is_live_status(pm.get("status")) and match_date == today - timedelta(days=1)

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.collect_once()
            except Exception as exc:
                self._last_error = exc.__class__.__name__
            self._next_run_at = _utc_after(self._interval_seconds)
            wakeup = self._wakeup
            if wakeup is None:
                await asyncio.sleep(self._interval_seconds)
                continue
            wakeup.clear()
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _write_pm(self, guid: str, pm: dict[str, Any]) -> None:
        previous = await self._store.get_json(f"pm:match:{guid}") or {}
        row = _merge_pm_realtime_fields(previous, dict(pm, guid=guid))
        await self._store.set_json(f"pm:match:{guid}", row, ttl_seconds=MATCH_STATE_TTL_SECONDS)

    async def external_candidates_for_match(
        self,
        guid: str,
        *,
        source: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """按时间和队名相似度列出外部比赛候选，供前端人工绑定。

        这里故意不看联赛名：足球同两队短时间内重复交手的概率很低，联赛别名差异
        反而容易把正确比赛过滤掉。
        """

        if self._store is None:
            raise RuntimeError("Collector requires a store")
        pm = await self._store.get_json(f"pm:match:{guid}") or {}
        if not pm:
            raise ValueError("match not found")
        selected_source = source if source in {"gs", "asa"} else self._external_source
        if selected_source not in {"gs", "asa"}:
            return []
        external_matches = await self._fetch_external_matches(selected_source, self._now_datetime())
        candidates = [
            _candidate_row(pm, row)
            for row in external_matches
            if _external_match_id(row)
        ]
        candidates = [row for row in candidates if row["confidence"] > 0]
        candidates.sort(key=lambda row: (-row["confidence"], row["time_delta_seconds"]))
        return candidates[: max(1, int(limit))]

    async def bind_external_match(self, guid: str, *, source: str, external_match_id: str) -> dict[str, Any]:
        """人工确认 PM 比赛与外部比赛的绑定关系，并写入当前态和索引。"""

        if self._store is None:
            raise RuntimeError("Collector requires a store")
        if source not in {"gs", "asa"}:
            raise ValueError("unsupported external source")
        pm = await self._store.get_json(f"pm:match:{guid}") or {}
        if not pm:
            raise ValueError("match not found")
        candidates = await self.external_candidates_for_match(guid, source=source, limit=200)
        selected = next((row for row in candidates if row["external_match_id"] == str(external_match_id)), None)
        if selected is None:
            raise ValueError("external match not found")
        external_matches = await self._fetch_external_matches(source, self._now_datetime())
        external = next(
            (row for row in external_matches if _external_match_id(row) == str(external_match_id)),
            None,
        )
        if external is None:
            raise ValueError("external match not found")
        now = _utc_now()
        external = dict(external, guid=guid)
        if source == "asa":
            await self._write_asa(guid, external)
            await self._write_asa_indices(guid, external)
        else:
            await self._write_gs(guid, external)
            await self._write_gs_indices(guid, external)
        binding = _binding(guid, pm, external, float(selected["confidence"]), "matched", now)
        await self._store.set_json(f"binding:{guid}", binding, ttl_seconds=MATCH_STATE_TTL_SECONDS)
        await self._store.delete(f"idx:match:status:pending:{guid}")
        await self._store.set_text(
            f"idx:match:status:matched:{guid}",
            guid,
            ttl_seconds=MATCH_STATE_TTL_SECONDS,
        )
        await self._store.set_text(f"idx:guid:{guid}", guid, ttl_seconds=MATCH_STATE_TTL_SECONDS)
        return binding

    async def _fetch_external_matches(self, source: str, now_dt: datetime) -> list[dict[str, Any]]:
        updated_at = _format_utc(now_dt)
        if source == "gs":
            raw_rows = [
                *[row for row in await self._gs_client.fetch_home() if _sport(row) == "football"],
                *[row for row in await self._gs_client.fetch_d1() if _sport(row) == "football"],
            ]
            return [_parse_gs_match(row, updated_at) for row in raw_rows]
        if source == "asa":
            raw_rows = [
                *[row for row in await self._asa_client.fetch_home() if _sport(row) == "football"],
                *[row for row in await self._asa_client.fetch_d1() if _sport(row) == "football"],
            ]
            return [_parse_asa_match(row, updated_at) for row in raw_rows]
        return []

    async def _publish_pm_snapshot(
        self,
        guid: str,
        pm: dict[str, Any],
        snapshot: dict[str, Any],
        binding: dict[str, Any] | None = None,
    ) -> None:
        if self._broadcaster is None:
            return
        await self._broadcaster.publish(
            {"topic": "match.snapshot", "payload": _with_binding_fields(_match_card(guid, pm), binding or {})}
        )
        await self._broadcaster.publish({"topic": "chart.snapshot", "payload": snapshot})

    async def _write_gs(self, guid: str, gs: dict[str, Any]) -> None:
        await self._store.set_json(f"gs:match:{guid}", gs, ttl_seconds=MATCH_STATE_TTL_SECONDS)
        await self._store.set_json(f"external:match:{guid}", gs, ttl_seconds=MATCH_STATE_TTL_SECONDS)

    async def _write_asa(self, guid: str, asa: dict[str, Any]) -> None:
        await self._store.set_json(f"asa:match:{guid}", asa, ttl_seconds=MATCH_STATE_TTL_SECONDS)
        await self._store.set_json(f"external:match:{guid}", asa, ttl_seconds=MATCH_STATE_TTL_SECONDS)

    async def _write_pm_indices(self, guid: str, pm: dict[str, Any]) -> None:
        await self._store.set_text(f"idx:pm:event:{pm['pm_event_id']}", guid, ttl_seconds=MATCH_STATE_TTL_SECONDS)
        await self._store.set_text(f"idx:pm:slug:{pm['slug']}", guid, ttl_seconds=MATCH_STATE_TTL_SECONDS)
        game_id = pm.get("raw", {}).get("game_id") if isinstance(pm.get("raw"), dict) else None
        if game_id:
            await self._store.set_text(f"idx:pm:game:{game_id}", guid, ttl_seconds=MATCH_STATE_TTL_SECONDS)
        for outcome in ("home", "draw", "away"):
            asset_id = pm.get(f"{outcome}_asset_id")
            if asset_id:
                await self._store.set_text(
                    f"idx:pm:asset:{asset_id}",
                    f"{guid}|{outcome}",
                    ttl_seconds=MATCH_STATE_TTL_SECONDS,
                )

    async def _cached_unbound_pm_matches(self, now_dt: datetime) -> list[dict[str, Any]]:
        """补处理仍在 TTL 内但当前 PM HTTP 不再返回的待绑定比赛。

        PM 会在比赛结束或市场状态变化后不再出现在当前采集列表里；如果这些比赛
        之前因为外部源未匹配而处于 pending，Collector 仍应在 3 天保留期内继续尝试
        用 ASA/GS 绑定，避免错过赛中或完赛后的外部赛况。
        """

        rows: list[dict[str, Any]] = []
        for key in await self._store.keys("pm:match:*"):
            guid = key.rsplit(":", 1)[-1]
            binding = await self._store.get_json(f"binding:{guid}") or {}
            if binding.get("external_source"):
                continue
            pm = await self._store.get_json(key) or {}
            if not isinstance(pm, dict) or not pm.get("pm_event_id"):
                continue
            if self._passes_pm_filter(pm, now_dt):
                rows.append(pm)
        return rows

    async def _write_gs_indices(self, guid: str, gs: dict[str, Any]) -> None:
        for key, prefix in (
            ("gs_match_id", "idx:gs:id"),
            ("gs_pregame_id", "idx:gs:pregame"),
            ("gs_inplay_id", "idx:gs:inplay"),
        ):
            value = gs.get(key)
            if value:
                await self._store.set_text(f"{prefix}:{value}", guid, ttl_seconds=MATCH_STATE_TTL_SECONDS)

    async def _write_asa_indices(self, guid: str, asa: dict[str, Any]) -> None:
        for key, prefix in (
            ("asa_match_id", "idx:asa:id"),
            ("asa_pregame_id", "idx:asa:pregame"),
            ("asa_inplay_id", "idx:asa:inplay"),
        ):
            value = asa.get(key)
            if value:
                await self._store.set_text(f"{prefix}:{value}", guid, ttl_seconds=MATCH_STATE_TTL_SECONDS)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _utc_after(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _sport(row: dict[str, Any]) -> str:
    return str(row.get("sport") or row.get("sport_name") or "football").lower()


def _guid_for(pm_event_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", pm_event_id).strip("-").lower()
    return f"guid-{safe}"


def _score(row: dict[str, Any], side: str) -> int | None:
    score = row.get("score") or {}
    if isinstance(score, dict):
        return score.get(side)
    return row.get(f"score_{side}") or row.get(f"{side}_score")


def _parse_pm_match(row: dict[str, Any], updated_at: str) -> dict[str, Any]:
    market = row.get("market") or {}
    tokens = market.get("tokens") or {}
    prices = market.get("prices") or {}
    volume = row.get("volume") or {}
    moneyline_volume = _float_value(volume.get("moneyline", row.get("moneyline_volume", 0)))
    total_volume = _float_value(volume.get("total", row.get("total_volume", 0)))
    parsed = {
        "pm_event_id": str(row["id"]),
        "slug": str(row.get("slug") or row["id"]),
        "sport": "football",
        "league": row.get("league") or "",
        "home_team": row.get("home_team") or row.get("home") or "",
        "away_team": row.get("away_team") or row.get("away") or "",
        "start_time_utc": row.get("start_time") or row.get("start_time_utc") or "",
        "status": row.get("status") or "scheduled",
        "updated_at_utc": updated_at,
        "score_home": _score(row, "home"),
        "score_away": _score(row, "away"),
        "total_volume": total_volume,
        "moneyline_volume": moneyline_volume,
        "condition_id": market.get("condition_id") or row.get("condition_id"),
        "home_asset_id": tokens.get("home") or row.get("home_asset_id"),
        "draw_asset_id": tokens.get("draw") or row.get("draw_asset_id"),
        "away_asset_id": tokens.get("away") or row.get("away_asset_id"),
        "home_ask1": _price(prices, "home", "ask"),
        "home_bid1": _price(prices, "home", "bid"),
        "draw_ask1": _price(prices, "draw", "ask"),
        "draw_bid1": _price(prices, "draw", "bid"),
        "away_ask1": _price(prices, "away", "ask"),
        "away_bid1": _price(prices, "away", "bid"),
        "raw": row,
    }
    finished_at_utc = _source_updated_at(row)
    if _is_finished_status(parsed["status"]) and finished_at_utc:
        parsed["finished_at_utc"] = finished_at_utc
    home_logo_url = (row.get("team_logos") or {}).get("home") or row.get("home_logo_url")
    away_logo_url = (row.get("team_logos") or {}).get("away") or row.get("away_logo_url")
    if home_logo_url:
        parsed["home_logo_url"] = home_logo_url
    if away_logo_url:
        parsed["away_logo_url"] = away_logo_url
    return parsed


def _merge_pm_realtime_fields(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    if not previous:
        if _is_finished_status(current.get("status")) and not current.get("finished_at_utc"):
            current["finished_at_utc"] = current.get("updated_at_utc")
        return current
    if previous.get("finished_at_utc") and not current.get("finished_at_utc"):
        current["finished_at_utc"] = previous["finished_at_utc"]
    if _is_finished_status(current.get("status")) and not current.get("finished_at_utc"):
        current["finished_at_utc"] = current.get("updated_at_utc")
    for field in ("match_time", "period", "clock"):
        if not current.get(field) and previous.get(field):
            current[field] = previous[field]
    if _is_live_status(previous.get("status")) and not _is_finished_status(current.get("status")):
        if not _is_live_status(current.get("status")):
            current["status"] = previous.get("status")
        for field in ("score_home", "score_away"):
            if previous.get(field) is not None:
                current[field] = previous[field]
    return current


def _parse_gs_match(row: dict[str, Any], updated_at: str) -> dict[str, Any]:
    odds = row.get("odds") or {}
    return {
        "source": "gs",
        "external_match_id": str(row.get("match_id") or ""),
        "gs_match_id": str(row.get("match_id") or ""),
        "gs_pregame_id": str(row.get("pregame_id") or ""),
        "gs_inplay_id": str(row.get("inplay_id") or ""),
        "pm_game_id": row.get("pm_game_id"),
        "league": row.get("league") or "",
        "home_team": row.get("home_team") or row.get("home") or "",
        "away_team": row.get("away_team") or row.get("away") or "",
        "start_time_utc": row.get("start_time") or row.get("start_time_utc") or "",
        "status": row.get("status") or "scheduled",
        "updated_at_utc": updated_at,
        "score_home": _score(row, "home"),
        "score_away": _score(row, "away"),
        "match_time": row.get("match_time") or "",
        "period": row.get("period") or "",
        "clock": row.get("clock") or "",
        "odds_home": odds.get("home", row.get("odds_home")),
        "odds_draw": odds.get("draw", row.get("odds_draw")),
        "odds_away": odds.get("away", row.get("odds_away")),
        "red_cards": row.get("red_cards"),
        "yellow_cards": row.get("yellow_cards"),
        "corners": row.get("corners"),
        "shots_on_target": row.get("shots_on_target"),
        "events": row.get("events"),
        "lineups": row.get("lineups"),
        "commentary_available": row.get("commentary_available"),
        "venue": row.get("venue"),
        "substitutions": row.get("substitutions"),
        "var_events": row.get("var_events"),
        "penalties": row.get("penalties"),
        "free_kicks": row.get("free_kicks"),
        "raw": row,
    }


def _parse_asa_match(row: dict[str, Any], updated_at: str) -> dict[str, Any]:
    return {
        "source": "asa",
        "external_match_id": str(row.get("match_id") or ""),
        "asa_match_id": str(row.get("match_id") or ""),
        "asa_pregame_id": str(row.get("pregame_id") or row.get("match_id") or ""),
        "asa_inplay_id": str(row.get("inplay_id") or row.get("match_id") or ""),
        "league": row.get("league") or "",
        "home_team": row.get("home_team") or row.get("home") or "",
        "away_team": row.get("away_team") or row.get("away") or "",
        "home_logo_url": row.get("home_logo_url"),
        "away_logo_url": row.get("away_logo_url"),
        "start_time_utc": row.get("start_time") or row.get("start_time_utc") or "",
        "status": row.get("status") or "scheduled",
        "updated_at_utc": updated_at,
        "score_home": _score(row, "home"),
        "score_away": _score(row, "away"),
        "match_time": row.get("match_time") or "",
        "period": row.get("period") or "",
        "clock": row.get("clock") or "",
        "red_cards": row.get("red_cards"),
        "yellow_cards": row.get("yellow_cards"),
        "corners": row.get("corners"),
        "shots_on_target": row.get("shots_on_target"),
        "events": row.get("events"),
        "lineups": row.get("lineups"),
        "venue": row.get("venue"),
        "substitutions": row.get("substitutions"),
        "var_events": row.get("var_events"),
        "penalties": row.get("penalties"),
        "free_kicks": row.get("free_kicks"),
        "raw": row.get("raw", row),
    }


def _price(prices: dict[str, Any], outcome: str, side: str) -> float | None:
    value = (prices.get(outcome) or {}).get(side)
    return None if value is None else float(value)


def _match_gs(
    pm: dict[str, Any],
    gs_matches: list[dict[str, Any]],
    used_gs_ids: set[str],
) -> MatchCandidate | None:
    for gs in gs_matches:
        if _external_match_id(gs) in used_gs_ids:
            continue
        if pm.get("raw", {}).get("game_id") is not None and pm["raw"].get("game_id") == gs.get("pm_game_id"):
            return MatchCandidate(gs=gs, confidence=1.0, time_delta_seconds=0)

    best: MatchCandidate | None = None
    for gs in gs_matches:
        if _external_match_id(gs) in used_gs_ids:
            continue
        if abs(_epoch(pm["start_time_utc"]) - _epoch(gs["start_time_utc"])) > 45 * 60:
            continue
        home = _team_similarity(pm["home_team"], gs["home_team"])
        away = _team_similarity(pm["away_team"], gs["away_team"])
        time_delta = abs(_epoch(pm["start_time_utc"]) - _epoch(gs["start_time_utc"]))
        confidence = round((home + away) / 2, 4)
        if confidence >= 0.72 and (best is None or confidence > best.confidence):
            best = MatchCandidate(gs=gs, confidence=confidence, time_delta_seconds=int(time_delta))
    return best


def _candidate_row(pm: dict[str, Any], external: dict[str, Any]) -> dict[str, Any]:
    time_delta = abs(_epoch(pm.get("start_time_utc") or "") - _epoch(external.get("start_time_utc") or ""))
    home = _team_similarity(pm.get("home_team") or "", external.get("home_team") or "")
    away = _team_similarity(pm.get("away_team") or "", external.get("away_team") or "")
    team_score = (home + away) / 2
    # 人工候选排序可容忍较宽时间差，但时间越接近排得越靠前。
    time_score = max(0.0, 1.0 - time_delta / (6 * 60 * 60))
    confidence = round(team_score * 0.85 + time_score * 0.15, 4)
    return {
        "source": external.get("source") or "gs",
        "external_match_id": _external_match_id(external),
        "league": external.get("league") or "",
        "home_team": external.get("home_team") or "",
        "away_team": external.get("away_team") or "",
        "start_time_utc": external.get("start_time_utc") or "",
        "status": external.get("status") or "",
        "score_home": external.get("score_home"),
        "score_away": external.get("score_away"),
        "match_time": external.get("match_time") or external.get("clock") or "",
        "confidence": confidence,
        "team_confidence": round(team_score, 4),
        "time_delta_seconds": int(time_delta),
    }


def _external_match_id(row: dict[str, Any]) -> str:
    return str(row.get("external_match_id") or row.get("gs_match_id") or row.get("asa_match_id") or "")


def _binding(
    guid: str,
    pm: dict[str, Any],
    gs: dict[str, Any] | None,
    confidence: float,
    status: str,
    now: str,
) -> dict[str, Any]:
    return {
        "guid": guid,
        "pm_event_id": pm["pm_event_id"],
        "pm_slug": pm["slug"],
        "pm_condition_id": pm["condition_id"],
        "pm_home_asset_id": pm["home_asset_id"],
        "pm_draw_asset_id": pm["draw_asset_id"],
        "pm_away_asset_id": pm["away_asset_id"],
        "gs_match_id": gs.get("gs_match_id") if gs else None,
        "gs_pregame_id": gs.get("gs_pregame_id") if gs else None,
        "gs_inplay_id": gs.get("gs_inplay_id") if gs else None,
        "external_source": gs.get("source") if gs else None,
        "external_match_id": _external_match_id(gs) if gs else None,
        "asa_match_id": gs.get("asa_match_id") if gs else None,
        "asa_pregame_id": gs.get("asa_pregame_id") if gs else None,
        "asa_inplay_id": gs.get("asa_inplay_id") if gs else None,
        "confidence": confidence,
        "status": status,
        "created_at_utc": now,
        "updated_at_utc": now,
    }


def _match_card(guid: str, pm: dict[str, Any]) -> dict[str, Any]:
    moneyline_volume = _float_value(pm.get("moneyline_volume"))
    total_volume = _float_value(pm.get("total_volume"))
    raw = pm.get("raw") if isinstance(pm.get("raw"), dict) else {}
    return {
        "match_id": guid,
        "sport": "football",
        "league": pm.get("league") or "",
        "team_home": pm.get("home_team") or "",
        "team_away": pm.get("away_team") or "",
        "home_logo_url": pm.get("home_logo_url") or _team_logo_url(pm, "home"),
        "away_logo_url": pm.get("away_logo_url") or _team_logo_url(pm, "away"),
        "start_time_utc": pm.get("start_time_utc") or "",
        "status": pm.get("status") or "",
        "moneyline_volume": moneyline_volume,
        "total_volume": total_volume,
        "latest_ts_utc": pm.get("updated_at_utc") or _utc_now(),
        "score_home": pm.get("score_home"),
        "score_away": pm.get("score_away"),
        "pm_match_time": pm.get("match_time"),
        "external_event_id": pm.get("pm_event_id"),
        "external_event_slug": pm.get("slug"),
        "external_market_id": pm.get("condition_id"),
        "external_market_slug": pm.get("slug"),
        "game_id": raw.get("game_id") or raw.get("gameId"),
        "home_bid": pm.get("home_bid1"),
        "home_ask": pm.get("home_ask1"),
        "home_price": _mid(pm.get("home_bid1"), pm.get("home_ask1")),
        "away_bid": pm.get("away_bid1"),
        "away_ask": pm.get("away_ask1"),
        "away_price": _mid(pm.get("away_bid1"), pm.get("away_ask1")),
        "draw_bid": pm.get("draw_bid1"),
        "draw_ask": pm.get("draw_ask1"),
        "draw_price": _mid(pm.get("draw_bid1"), pm.get("draw_ask1")),
    }


def _with_binding_fields(card: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
    card["external_source"] = binding.get("external_source")
    card["external_match_id"] = binding.get("external_match_id")
    card["external_bound"] = bool(
        binding.get("status") == "matched"
        and binding.get("external_source")
        and binding.get("external_match_id")
    )
    card["binding_status"] = binding.get("status") or "pending"
    return card


def _team_logo_url(pm: dict[str, Any], side: str) -> str | None:
    raw = pm.get("raw") if isinstance(pm.get("raw"), dict) else {}
    target = _normalize_team(pm.get(f"{side}_team") or "")
    candidates = [raw.get("teams")]
    if isinstance(raw.get("raw"), dict):
        candidates.append(raw["raw"].get("teams"))
    for teams in candidates:
        for team in teams or []:
            if not isinstance(team, dict):
                continue
            name = _normalize_team(str(team.get("name") or team.get("title") or team.get("displayName") or ""))
            logo = team.get("logo") or team.get("image") or team.get("icon") or team.get("logoUrl")
            if target and name == target and logo:
                return str(logo)
    return None


def _epoch(value: str) -> float:
    if not value:
        return 0
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).timestamp()


def _team_similarity(left: str, right: str) -> float:
    left_variants = _team_variants(left)
    right_variants = _team_variants(right)
    if left_variants & right_variants:
        return 1.0
    left_tokens = set(_normalize_team(left).split())
    right_tokens = set(_normalize_team(right).split())
    if left_tokens and right_tokens and (left_tokens <= right_tokens or right_tokens <= left_tokens):
        return 0.92
    return max(
        SequenceMatcher(None, left_value, right_value).ratio()
        for left_value in left_variants
        for right_value in right_variants
    )


def _normalize_team(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("utd", "united")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [
        token
        for token in text.split()
        if token
        and not token.isdigit()
        and token
        not in {
            "fc",
            "cf",
            "club",
            "team",
            "ca",
            "cd",
            "ac",
            "sc",
            "afc",
            "as",
            "calcio",
            "football",
            "soccer",
            "de",
            "la",
            "the",
        }
    ]
    return " ".join(tokens)


def _team_variants(value: str) -> set[str]:
    normalized = _normalize_team(value)
    tokens = set(normalized.split())
    variants = {normalized} if normalized else {""}
    if "internazionale" in tokens:
        variants.update({"inter", "inter milan", "inter milano"})
    if normalized in {"inter", "inter milan", "inter milano"}:
        variants.update({"internazionale", "internazionale milano"})
    if "parma" in tokens:
        variants.add("parma")
    if {"paris", "saint", "germain"} <= tokens or "psg" in tokens:
        variants.update({"psg", "paris saint germain"})
    if normalized in {"psg", "paris saint germain"}:
        variants.update({"psg", "paris saint germain"})
    return {variant for variant in variants if variant}


def _mid(bid: Any, ask: Any) -> float | None:
    if bid is None or ask is None:
        return None
    return round((float(bid) + float(ask)) / 2, 6)


def _is_live_status(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "live",
        "inprogress",
        "in_progress",
        "in-play",
        "inplay",
        "break",
        "halftime",
        "half_time",
        "ht",
    }


def _is_finished_status(value: Any) -> bool:
    return str(value or "").strip().lower() in {"finished", "final", "ended", "closed", "complete", "completed"}


def _source_updated_at(row: dict[str, Any]) -> str | None:
    for key in ("finished_at_utc", "finishedAt", "updated_at_utc", "updatedAt", "updated_at"):
        value = row.get(key)
        if value:
            return str(value)
    return None


def _finished_for_more_than(pm: dict[str, Any], now_dt: datetime, *, minutes: int) -> bool:
    finished_at = _parse_datetime(str(pm.get("finished_at_utc") or ""))
    if finished_at is None:
        return False
    return now_dt - finished_at > timedelta(minutes=minutes)
