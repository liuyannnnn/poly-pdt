"""PM 盘口时序数据：采集器原样追加，WS tick 原样追加，tick 图表单独降采样。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Callable


TEN_SECONDS = 10
MATCH_RELATED_TTL_SECONDS = 3 * 24 * 60 * 60
COLLECTOR_SERIES_TTL_SECONDS = MATCH_RELATED_TTL_SECONDS
TEN_SECOND_SERIES_TTL_SECONDS = MATCH_RELATED_TTL_SECONDS
TICK_SERIES_TTL_SECONDS = MATCH_RELATED_TTL_SECONDS
CURRENT_STATE_TTL_SECONDS = COLLECTOR_SERIES_TTL_SECONDS
MAX_COLLECTOR_ROWS = 20_000
MAX_TEN_SECOND_ROWS = 5 * 24 * 60 * 6
MAX_TICK_ROWS: int | None = None
logger = logging.getLogger(__name__)


async def append_pm_collector_snapshot(
    store: Any,
    guid: str,
    pm: dict[str, Any],
    ts_utc: str | datetime | None,
) -> dict[str, Any]:
    """把每次 PM HTTP 采集结果按真实采集时间追加到 Redis。"""

    snapshot_ts = normalize_ts(ts_utc)
    row = _snapshot_from_pm(
        guid,
        pm,
        snapshot_ts_utc=snapshot_ts,
        source="pm_http",
        phase="ALL",
        ingest_type="collector_snapshot",
    )
    await _append_series_row(
        store,
        f"series:pm:collector:{guid}",
        row,
        ttl_seconds=COLLECTOR_SERIES_TTL_SECONDS,
        max_rows=MAX_COLLECTOR_ROWS,
    )
    return row


async def append_pm_tick_snapshot(
    store: Any,
    guid: str,
    pm: dict[str, Any],
    ts_utc: str | datetime | None,
    before_persist: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """把 PM market WS tick 保存为原始 tick 序列。

    tick 序列保留 72 小时用于赛后复盘；界面图表使用 10 秒重采样序列，
    避免原始 tick 过多时拖慢前端。
    """

    snapshot_ts = normalize_ts(ts_utc)
    row = _snapshot_from_pm(
        guid,
        pm,
        snapshot_ts_utc=snapshot_ts,
        source="pm_market",
        phase="LIVE",
        ingest_type="market_tick",
    )
    if before_persist is not None:
        before_persist(dict(row))
    await _append_series_row(
        store,
        f"series:pm:ticks:{guid}",
        row,
        ttl_seconds=TICK_SERIES_TTL_SECONDS,
        max_rows=MAX_TICK_ROWS,
    )
    return row


async def resample_tick_series(store: Any) -> dict[str, int]:
    """把每场比赛当前最新 tick 追加到 10 秒表。"""

    matched = 0
    total_rows = 0
    for tick_key in await store.keys("series:pm:ticks:*"):
        guid = tick_key.rsplit(":", 1)[-1]
        ticks = await get_series_rows(store, tick_key, limit=1)
        if not isinstance(ticks, list) or not ticks:
            continue
        latest = ticks[-1]
        if not isinstance(latest, dict):
            continue
        resampled_row = _market_10s_row(latest)
        existing = await get_series_rows(store, f"series:pm:10s:{guid}", limit=1)
        if existing and isinstance(existing[-1], dict):
            if _row_fingerprint(existing[-1]) == _row_fingerprint(resampled_row):
                matched += 1
                continue
        appended = await _append_resampled_rows(
            store,
            f"series:pm:10s:{guid}",
            [resampled_row],
            ttl_seconds=TEN_SECOND_SERIES_TTL_SECONDS,
            max_rows=MAX_TEN_SECOND_ROWS,
        )
        matched += 1
        total_rows += appended

    return {"matches": matched, "rows": total_rows}


class TimeseriesResampler:
    """后台 10 秒重采样任务。"""

    def __init__(
        self,
        store: Any,
        *,
        interval_seconds: float = TEN_SECONDS,
        resampler: Callable[[Any], Any] = resample_tick_series,
        broadcaster: Any | None = None,
    ):
        self._store = store
        self._interval_seconds = interval_seconds
        self._resampler = resampler
        self._broadcaster = broadcaster
        self._latest_tick_by_guid: dict[str, dict[str, Any]] = {}
        self._last_10s_md5_by_guid: dict[str, str] = {}
        self._last_published_ts_by_key: dict[str, str] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def resample_once(self) -> dict[str, int]:
        if self._latest_tick_by_guid:
            return await self._resample_from_memory()
        summary = await self._resampler(self._store)
        await self._publish_latest_live_rows()
        return summary

    def observe_tick(self, row: dict[str, Any]) -> None:
        guid = str(row.get("match_id") or "")
        if not guid:
            return
        self._latest_tick_by_guid[guid] = dict(row)

    def forget_match(self, guid: str) -> None:
        self._latest_tick_by_guid.pop(guid, None)
        self._last_10s_md5_by_guid.pop(guid, None)
        self._last_published_ts_by_key.pop(f"series:pm:10s:{guid}", None)

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.resample_once()
            except Exception as exc:
                await _write_system_error(self._store, "timeseries_resampler", exc)
            await asyncio.sleep(self._interval_seconds)

    async def _resample_from_memory(self) -> dict[str, int]:
        matched = 0
        total_rows = 0
        for guid, latest in list(self._latest_tick_by_guid.items()):
            if not isinstance(latest, dict):
                continue
            row = _market_10s_row(latest)
            row_md5 = _row_fingerprint(row)
            if self._last_10s_md5_by_guid.get(guid) == row_md5:
                matched += 1
                continue
            appended = await _append_resampled_rows(
                self._store,
                f"series:pm:10s:{guid}",
                [row],
                ttl_seconds=TEN_SECOND_SERIES_TTL_SECONDS,
                max_rows=MAX_TEN_SECOND_ROWS,
            )
            matched += 1
            total_rows += appended
            if not appended:
                continue
            self._last_10s_md5_by_guid[guid] = row_md5
            if self._broadcaster is not None:
                await self._broadcaster.publish({"topic": "chart.snapshot", "payload": row})
        return {"matches": matched, "rows": total_rows}

    async def _publish_latest_live_rows(self) -> None:
        if self._broadcaster is None:
            return
        for key in await self._store.keys("series:pm:10s:*"):
            rows = await get_series_rows(self._store, key, limit=1)
            if not isinstance(rows, list) or not rows:
                continue
            latest = rows[-1]
            if not isinstance(latest, dict):
                continue
            snapshot_ts = str(latest.get("snapshot_ts_utc") or "")
            if not snapshot_ts:
                continue
            if self._last_published_ts_by_key.get(key) == snapshot_ts:
                continue
            self._last_published_ts_by_key[key] = snapshot_ts
            await self._broadcaster.publish({"topic": "chart.snapshot", "payload": latest})


def normalize_ts(value: str | datetime | None) -> str:
    parsed = _parse_datetime(value)
    return parsed.isoformat().replace("+00:00", "Z")


async def get_series_rows(store: Any, key: str, limit: int | None = None) -> list[Any]:
    if hasattr(store, "get_json_list"):
        rows = await store.get_json_list(key, limit=limit)
    else:
        rows = await store.get_json(key) or []
        if limit is not None and limit > 0 and isinstance(rows, list):
            rows = rows[-limit:]
    return rows if isinstance(rows, list) else []


def _snapshot_from_pm(
    guid: str,
    pm: dict[str, Any],
    *,
    snapshot_ts_utc: str,
    source: str,
    phase: str,
    ingest_type: str,
) -> dict[str, Any]:
    raw = pm.get("raw") if isinstance(pm.get("raw"), dict) else {}
    return {
        "match_id": guid,
        "snapshot_ts_utc": snapshot_ts_utc,
        "source": source,
        "sport": pm.get("sport") or "football",
        "status": pm.get("status") or "",
        "phase": phase,
        "start_time_utc": pm.get("start_time_utc") or "",
        "team_home": pm.get("home_team") or "",
        "team_away": pm.get("away_team") or "",
        "external_event_id": pm.get("pm_event_id"),
        "external_event_slug": pm.get("slug"),
        "external_market_id": pm.get("condition_id"),
        "external_market_slug": pm.get("slug"),
        "game_id": raw.get("game_id") or raw.get("gameId"),
        "score_home": pm.get("score_home"),
        "score_away": pm.get("score_away"),
        "home_bid": pm.get("home_bid1"),
        "home_ask": pm.get("home_ask1"),
        "home_price": _mid(pm.get("home_bid1"), pm.get("home_ask1")),
        "draw_bid": pm.get("draw_bid1"),
        "draw_ask": pm.get("draw_ask1"),
        "draw_price": _mid(pm.get("draw_bid1"), pm.get("draw_ask1")),
        "away_bid": pm.get("away_bid1"),
        "away_ask": pm.get("away_ask1"),
        "away_price": _mid(pm.get("away_bid1"), pm.get("away_ask1")),
        "moneyline_volume": pm.get("moneyline_volume") or 0,
        "total_volume": pm.get("total_volume") or 0,
        "ingest_type": ingest_type,
    }


async def _append_series_row(
    store: Any,
    key: str,
    row: dict[str, Any],
    *,
    ttl_seconds: int,
    max_rows: int | None,
) -> None:
    if hasattr(store, "append_json_list_item"):
        await store.append_json_list_item(key, row, ttl_seconds=ttl_seconds, max_rows=max_rows)
        return
    raise RuntimeError("series store must support append_json_list_item")


async def _write_system_error(store: Any, component: str, exc: Exception) -> None:
    logger.error("%s failed", component, exc_info=(type(exc), exc, exc.__traceback__))
    if not hasattr(store, "add_stream"):
        return
    with suppress(Exception):
        await store.add_stream(
            "stream:system_logs",
            {
                "source": "SYS",
                "component": component,
                "level": "error",
                "error_type": exc.__class__.__name__,
                "message": str(exc),
                "ts_utc": normalize_ts(None),
            },
        )


async def _append_resampled_rows(
    store: Any,
    key: str,
    rows: list[dict[str, Any]],
    *,
    ttl_seconds: int,
    max_rows: int,
) -> int:
    seen_ts: set[str] = set()
    appended = 0
    for row in sorted(rows, key=lambda item: _epoch(item.get("snapshot_ts_utc"))):
        snapshot_ts = str(row.get("snapshot_ts_utc") or "")
        if not snapshot_ts or snapshot_ts in seen_ts:
            continue
        await _append_series_row(store, key, row, ttl_seconds=ttl_seconds, max_rows=max_rows)
        seen_ts.add(snapshot_ts)
        appended += 1
    return appended


def _window_start_epoch(value: str | datetime | None, seconds: int) -> int:
    parsed = _parse_datetime(value)
    epoch = int(parsed.timestamp())
    return epoch - (epoch % seconds)


def _market_10s_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "snapshot_ts_utc": row["snapshot_ts_utc"],
        "source": "pm_market",
        "phase": "LIVE",
        "ingest_type": "market_10s",
    }


def _row_fingerprint(row: dict[str, Any]) -> str:
    payload = json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _parse_datetime(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.isdigit():
            number = int(text)
            if number > 10_000_000_000:
                return datetime.fromtimestamp(number / 1000, UTC)
            return datetime.fromtimestamp(number, UTC)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _epoch(value: Any) -> float:
    return _parse_datetime(str(value or "")).timestamp()


def _mid(bid: Any, ask: Any) -> float | None:
    if bid is None or ask is None:
        return None
    return round((float(bid) + float(ask)) / 2, 6)
