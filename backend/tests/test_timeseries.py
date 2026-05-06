import asyncio

import pytest
from datetime import UTC, datetime, timedelta

from app.listener import BroadcastHub
from app.store import MemoryStore
from app.timeseries import (
    COLLECTOR_SERIES_TTL_SECONDS,
    MATCH_RELATED_TTL_SECONDS,
    TEN_SECOND_SERIES_TTL_SECONDS,
    TICK_SERIES_TTL_SECONDS,
    append_pm_collector_snapshot,
    append_pm_tick_snapshot,
    resample_tick_series,
    TimeseriesResampler,
)


PM_ROW = {
    "guid": "guid-match-1",
    "sport": "football",
    "status": "live",
    "start_time_utc": "2026-05-02T14:00:00Z",
    "home_team": "Brentford FC",
    "away_team": "West Ham United FC",
    "pm_event_id": "394220",
    "slug": "epl-bre-wes-2026-05-02",
    "condition_id": "cond",
    "score_home": 0,
    "score_away": 0,
    "home_bid1": 0.52,
    "home_ask1": 0.53,
    "draw_bid1": 0.23,
    "draw_ask1": 0.24,
    "away_bid1": 0.24,
    "away_ask1": 0.25,
    "moneyline_volume": 681362.91,
    "total_volume": 681362.91,
    "raw": {"game_id": 90091343},
}


class FastSeriesStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.append_calls: list[tuple[str, dict, int, int | None]] = []
        self.get_json_calls: list[str] = []

    async def append_json_list_item(self, key: str, value: dict, *, ttl_seconds: int, max_rows: int | None) -> None:
        self.append_calls.append((key, value, ttl_seconds, max_rows))
        rows = await super().get_json(key) or []
        rows.append(value)
        if max_rows is not None and max_rows > 0:
            rows = rows[-max_rows:]
        await super().set_json(key, rows, ttl_seconds=ttl_seconds)

    async def get_json(self, key: str):
        self.get_json_calls.append(key)
        return await super().get_json(key)


def _iso_at(offset_seconds: int) -> str:
    base = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=10)
    return (base + timedelta(seconds=offset_seconds)).isoformat().replace("+00:00", "Z")


@pytest.mark.asyncio
async def test_collector_snapshot_appends_raw_collection_rows_without_grouping():
    store = MemoryStore()
    existing_ts = _iso_at(0)
    first_ts = _iso_at(191)
    second_ts = _iso_at(299)
    await store.set_json(
        "series:pm:collector:guid-match-1",
        [
            {
                "match_id": "guid-match-1",
                "snapshot_ts_utc": existing_ts,
                "phase": "ALL",
                "ingest_type": "collector_snapshot",
                "home_ask": 0.51,
            }
        ],
    )

    await append_pm_collector_snapshot(
        store,
        "guid-match-1",
        PM_ROW,
        first_ts,
    )
    await append_pm_collector_snapshot(
        store,
        "guid-match-1",
        {**PM_ROW, "home_ask1": 0.54},
        second_ts,
    )

    rows = await store.get_json("series:pm:collector:guid-match-1")

    assert len(rows) == 3
    assert [row["snapshot_ts_utc"] for row in rows] == [
        existing_ts,
        first_ts,
        second_ts,
    ]
    assert all("bucket_ts_utc" not in row for row in rows)
    assert rows[2]["home_ask"] == 0.54
    assert rows[2]["phase"] == "ALL"
    assert rows[2]["ingest_type"] == "collector_snapshot"
    assert await store.ttl("series:pm:collector:guid-match-1") == COLLECTOR_SERIES_TTL_SECONDS


@pytest.mark.asyncio
async def test_tick_resampler_only_appends_the_current_latest_tick():
    store = MemoryStore()
    first_ts = _iso_at(2)
    second_ts = _iso_at(6)
    third_ts = _iso_at(22)
    await append_pm_tick_snapshot(store, "guid-match-1", PM_ROW, first_ts)
    await append_pm_tick_snapshot(
        store,
        "guid-match-1",
        {**PM_ROW, "home_ask1": 0.55},
        second_ts,
    )
    await append_pm_tick_snapshot(
        store,
        "guid-match-1",
        {**PM_ROW, "home_ask1": 0.58},
        third_ts,
    )

    summary = await resample_tick_series(store)
    second_summary = await resample_tick_series(store)
    rows = await store.get_json("series:pm:10s:guid-match-1")

    assert summary == {"matches": 1, "rows": 1}
    assert second_summary == {"matches": 1, "rows": 0}
    assert [row["snapshot_ts_utc"] for row in rows] == [third_ts]
    assert all("bucket_ts_utc" not in row for row in rows)
    assert rows[0]["home_ask"] == 0.58
    assert rows[0]["phase"] == "LIVE"
    assert rows[0]["ingest_type"] == "market_10s"
    assert await store.ttl("series:pm:ticks:guid-match-1") == MATCH_RELATED_TTL_SECONDS
    assert TICK_SERIES_TTL_SECONDS == MATCH_RELATED_TTL_SECONDS
    assert await store.ttl("series:pm:10s:guid-match-1") == TEN_SECOND_SERIES_TTL_SECONDS


@pytest.mark.asyncio
async def test_tick_snapshot_preserves_pm_millisecond_timestamp():
    store = MemoryStore()

    await append_pm_tick_snapshot(store, "guid-match-1", PM_ROW, "1777827625004")

    rows = await store.get_json("series:pm:ticks:guid-match-1")
    assert rows[-1]["snapshot_ts_utc"] == "2026-05-03T17:00:25.004000Z"


@pytest.mark.asyncio
async def test_tick_snapshot_uses_fast_append_when_store_supports_it():
    store = FastSeriesStore()

    await append_pm_tick_snapshot(store, "guid-match-1", PM_ROW, _iso_at(1))

    assert len(store.append_calls) == 1
    key, row, ttl_seconds, max_rows = store.append_calls[0]
    assert key == "series:pm:ticks:guid-match-1"
    assert row["ingest_type"] == "market_tick"
    assert ttl_seconds == TICK_SERIES_TTL_SECONDS
    assert max_rows is None
    assert "series:pm:ticks:guid-match-1" not in store.get_json_calls


@pytest.mark.asyncio
async def test_series_append_rejects_non_list_key_without_rewriting_history():
    store = MemoryStore()
    await store.set_json("series:pm:ticks:guid-match-1", {"legacy": True})

    with pytest.raises(TypeError, match="expected list"):
        await append_pm_tick_snapshot(store, "guid-match-1", PM_ROW, _iso_at(1))

    assert await store.get_json("series:pm:ticks:guid-match-1") == {"legacy": True}


@pytest.mark.asyncio
async def test_timeseries_resampler_publishes_new_live_chart_rows_once():
    store = MemoryStore()
    hub = BroadcastHub()
    snapshot_ts = _iso_at(2)
    await append_pm_tick_snapshot(store, "guid-match-1", PM_ROW, snapshot_ts)

    resampler = TimeseriesResampler(store=store, broadcaster=hub)

    await resampler.resample_once()
    first_messages = await hub.drain()
    await resampler.resample_once()
    second_messages = await hub.drain()

    assert len(first_messages) == 1
    assert first_messages[0]["topic"] == "chart.snapshot"
    assert first_messages[0]["payload"]["match_id"] == "guid-match-1"
    assert first_messages[0]["payload"]["snapshot_ts_utc"] == snapshot_ts
    assert first_messages[0]["payload"]["source"] == "pm_market"
    assert first_messages[0]["payload"]["phase"] == "LIVE"
    assert first_messages[0]["payload"]["ingest_type"] == "market_10s"
    assert first_messages[0]["payload"]["home_ask"] == 0.53
    assert second_messages == []


@pytest.mark.asyncio
async def test_timeseries_resampler_uses_in_memory_latest_tick_and_skips_duplicates():
    store = MemoryStore()
    hub = BroadcastHub()
    first_ts = _iso_at(2)
    second_ts = _iso_at(12)
    first_tick = await append_pm_tick_snapshot(store, "guid-match-1", PM_ROW, first_ts)
    second_tick = await append_pm_tick_snapshot(store, "guid-match-1", {**PM_ROW, "home_ask1": 0.57}, second_ts)
    resampler = TimeseriesResampler(store=store, broadcaster=hub)

    resampler.observe_tick(first_tick)
    first_summary = await resampler.resample_once()
    first_rows = await store.get_json("series:pm:10s:guid-match-1")
    await hub.drain()

    second_summary = await resampler.resample_once()
    second_messages = await hub.drain()

    resampler.observe_tick(second_tick)
    third_summary = await resampler.resample_once()
    third_rows = await store.get_json("series:pm:10s:guid-match-1")
    third_messages = await hub.drain()

    assert first_summary == {"matches": 1, "rows": 1}
    assert [row["snapshot_ts_utc"] for row in first_rows] == [first_ts]
    assert second_summary == {"matches": 1, "rows": 0}
    assert second_messages == []
    assert third_summary == {"matches": 1, "rows": 1}
    assert [row["snapshot_ts_utc"] for row in third_rows] == [first_ts, second_ts]
    assert third_rows[-1]["home_ask"] == 0.57
    assert len(third_messages) == 1
    assert third_messages[0]["topic"] == "chart.snapshot"
    assert third_messages[0]["payload"]["snapshot_ts_utc"] == second_ts


@pytest.mark.asyncio
async def test_timeseries_resampler_can_forget_finished_match_memory():
    store = MemoryStore()
    tick = await append_pm_tick_snapshot(store, "guid-match-1", PM_ROW, _iso_at(2))
    resampler = TimeseriesResampler(store=store)

    resampler.observe_tick(tick)
    assert await resampler.resample_once() == {"matches": 1, "rows": 1}

    resampler.forget_match("guid-match-1")

    assert await resampler.resample_once() == {"matches": 1, "rows": 0}


@pytest.mark.asyncio
async def test_timeseries_resampler_logs_background_errors_to_system_stream():
    store = MemoryStore()

    async def broken_resampler(_store):
        raise RuntimeError("resample broke")

    resampler = TimeseriesResampler(store=store, resampler=broken_resampler, interval_seconds=0.01)
    await resampler.start()
    try:
        await _wait_until_stream(store, "stream:system_logs")
    finally:
        await resampler.stop()

    rows = await store.stream("stream:system_logs")
    assert rows[-1]["source"] == "SYS"
    assert rows[-1]["component"] == "timeseries_resampler"
    assert rows[-1]["error_type"] == "RuntimeError"


async def _wait_until_stream(store: MemoryStore, key: str, timeout_seconds: float = 0.2) -> None:
    deadline = datetime.now(UTC).timestamp() + timeout_seconds
    while datetime.now(UTC).timestamp() < deadline:
        if await store.stream(key):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{key} did not receive rows")
