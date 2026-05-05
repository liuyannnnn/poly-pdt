import asyncio

import pytest

from app.collector import Collector, StaticGSHttpClient, StaticPMHttpClient
from app.store import MemoryStore
from app.timeseries import MATCH_RELATED_TTL_SECONDS

from .fixtures import GS_D1, GS_HOME, PM_EVENTS


@pytest.mark.asyncio
async def test_collector_matches_pm_to_gs_and_writes_isolated_redis_records():
    store = MemoryStore()
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient(PM_EVENTS),
        gs_client=StaticGSHttpClient(home=GS_HOME, d1=GS_D1),
    )

    report = await collector.collect_once()

    assert report["pm_seen"] == 3
    assert report["gs_seen"] == 2
    assert report["matched"] == 2
    assert report["pending"] == 1

    direct_guid = report["bindings"][0]["guid"]
    fuzzy_guid = report["bindings"][1]["guid"]
    pending_guid = report["pending_bindings"][0]["guid"]

    assert await store.get_json(f"pm:match:{direct_guid}") == {
        "guid": direct_guid,
        "pm_event_id": "pm-ars-che",
        "slug": "epl-arsenal-chelsea-2026-05-01",
        "sport": "football",
        "league": "Premier League",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "start_time_utc": "2026-05-01T19:00:00Z",
        "status": "scheduled",
        "updated_at_utc": report["completed_at_utc"],
        "score_home": 0,
        "score_away": 0,
        "total_volume": 300000,
        "moneyline_volume": 120000,
        "condition_id": "cond-ars-che",
        "home_asset_id": "asset-ars-home",
        "draw_asset_id": "asset-ars-draw",
        "away_asset_id": "asset-ars-away",
        "home_ask1": 0.44,
        "home_bid1": 0.43,
        "draw_ask1": 0.27,
        "draw_bid1": 0.25,
        "away_ask1": 0.33,
        "away_bid1": 0.31,
        "raw": PM_EVENTS[0],
    }
    gs_match = await store.get_json(f"gs:match:{direct_guid}")
    assert gs_match["guid"] == direct_guid
    assert gs_match["gs_match_id"] == "gs-100"
    assert gs_match["score_home"] == 0
    assert gs_match["odds_home"] == 2.1

    binding = await store.get_json(f"binding:{direct_guid}")
    assert binding["status"] == "matched"
    assert binding["pm_event_id"] == "pm-ars-che"
    assert binding["gs_match_id"] == "gs-100"
    assert binding["confidence"] == 1.0

    fuzzy_binding = await store.get_json(f"binding:{fuzzy_guid}")
    assert fuzzy_binding["status"] == "matched"
    assert fuzzy_binding["gs_match_id"] == "gs-200"
    assert fuzzy_binding["confidence"] >= 0.75

    pending_binding = await store.get_json(f"binding:{pending_guid}")
    assert pending_binding["status"] == "pending"
    assert pending_binding["gs_match_id"] is None

    assert await store.get_text("idx:pm:event:pm-ars-che") == direct_guid
    assert await store.get_text("idx:gs:id:gs-100") == direct_guid
    assert await store.get_text("idx:pm:asset:asset-ars-home") == f"{direct_guid}|home"
    assert await store.ttl(f"pm:match:{direct_guid}") == MATCH_RELATED_TTL_SECONDS
    assert await store.ttl(f"gs:match:{direct_guid}") == MATCH_RELATED_TTL_SECONDS
    assert await store.ttl(f"binding:{direct_guid}") == MATCH_RELATED_TTL_SECONDS

    collector_rows = await store.get_json(f"series:pm:collector:{direct_guid}")
    assert len(collector_rows) == 1
    assert collector_rows[0]["match_id"] == direct_guid
    assert collector_rows[0]["snapshot_ts_utc"] == report["completed_at_utc"]
    assert collector_rows[0]["phase"] == "ALL"
    assert collector_rows[0]["home_ask"] == 0.44
    assert collector_rows[0]["draw_ask"] == 0.27
    assert collector_rows[0]["away_ask"] == 0.33
    assert "bucket_ts_utc" not in collector_rows[0]
    assert await store.ttl(f"series:pm:collector:{direct_guid}") == MATCH_RELATED_TTL_SECONDS


@pytest.mark.asyncio
async def test_collector_ignores_non_football_without_basketball_scope():
    pm_payload = [
        {
            **PM_EVENTS[0],
            "id": "pm-nba",
            "sport": "basketball",
            "home_team": "Hawks",
            "away_team": "Magic",
        }
    ]
    collector = Collector(
        store=MemoryStore(),
        pm_client=StaticPMHttpClient(pm_payload),
        gs_client=StaticGSHttpClient(home=GS_HOME, d1=[]),
    )

    report = await collector.collect_once()

    assert report["pm_seen"] == 0
    assert report["matched"] == 0


@pytest.mark.asyncio
async def test_collector_can_use_asa_as_independent_external_source():
    store = MemoryStore()
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient(PM_EVENTS[:1]),
        gs_client=StaticGSHttpClient(home=[], d1=[]),
        asa_client=StaticGSHttpClient(
            home=[
                {
                    "source": "asa",
                    "match_id": "asa-100",
                    "pregame_id": "asa-100",
                    "inplay_id": "asa-100",
                    "league": "Premier League",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "start_time_utc": "2026-05-01T19:00:00Z",
                    "status": "scheduled",
                    "score": {"home": 0, "away": 0},
                }
            ],
            d1=[],
        ),
    )
    collector.set_external_source("asa")

    report = await collector.collect_once()
    guid = report["bindings"][0]["guid"]

    assert report["external_source"] == "asa"
    assert report["asa_seen"] == 1
    assert await store.get_json(f"gs:match:{guid}") is None
    asa_match = await store.get_json(f"asa:match:{guid}")
    assert asa_match["asa_match_id"] == "asa-100"
    assert asa_match["source"] == "asa"
    assert await store.get_text("idx:asa:id:asa-100") == guid
    binding = await store.get_json(f"binding:{guid}")
    assert binding["external_source"] == "asa"
    assert binding["asa_match_id"] == "asa-100"


@pytest.mark.asyncio
async def test_collector_matches_asa_when_league_alias_differs_but_teams_and_time_match():
    store = MemoryStore()
    pm_payload = [
        {
            **PM_EVENTS[0],
            "id": "394601",
            "slug": "lal-osa-bar-2026-05-02",
            "league": "La Liga 2025",
            "start_time": "2026-05-02T19:00:00Z",
            "home_team": "Ca Osasuna",
            "away_team": "Fc Barcelona",
            "volume": {"moneyline": 1_000_000, "total": 1_000_000},
        }
    ]
    asa_payload = [
        {
            "source": "asa",
            "match_id": "1608716",
            "pregame_id": "1608716",
            "inplay_id": "1608716",
            "league": "Primera",
            "home_team": "Osasuna",
            "away_team": "Barcelona",
            "start_time_utc": "2026-05-02T19:00:00Z",
            "status": "finished",
            "score": {"home": 1, "away": 2},
        }
    ]
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient(pm_payload),
        asa_client=StaticGSHttpClient(home=[], d1=asa_payload),
    )
    collector.set_external_source("asa")

    report = await collector.collect_once()
    guid = report["bindings"][0]["guid"]

    assert report["matched"] == 1
    assert report["pending"] == 0
    binding = await store.get_json(f"binding:{guid}")
    assert binding["external_source"] == "asa"
    assert binding["asa_match_id"] == "1608716"
    assert binding["confidence"] >= 0.86


@pytest.mark.asyncio
async def test_collector_matches_asa_short_names_to_pm_official_team_names():
    store = MemoryStore()
    pm_payload = [
        {
            **PM_EVENTS[0],
            "id": "sea-int-par-2026-05-03",
            "slug": "sea-int-par-2026-05-03",
            "league": "Serie A",
            "start_time": "2026-05-03T18:45:00Z",
            "home_team": "Fc Internazionale Milano",
            "away_team": "Parma Calcio 1913",
            "volume": {"moneyline": 1_000_000, "total": 1_000_000},
        }
    ]
    asa_payload = [
        {
            "source": "asa",
            "match_id": "1616912",
            "pregame_id": "1616912",
            "inplay_id": "1616912",
            "league": "Serie A",
            "home_team": "Inter",
            "away_team": "Parma",
            "start_time_utc": "2026-05-03T18:45:00Z",
            "status": "finished",
            "score": {"home": 0, "away": 0},
        }
    ]
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient(pm_payload),
        asa_client=StaticGSHttpClient(home=[], d1=asa_payload),
    )
    collector.set_external_source("asa")

    report = await collector.collect_once()
    guid = report["bindings"][0]["guid"]

    assert report["matched"] == 1
    binding = await store.get_json(f"binding:{guid}")
    assert binding["external_source"] == "asa"
    assert binding["asa_match_id"] == "1616912"
    assert binding["confidence"] == 1.0


@pytest.mark.asyncio
async def test_collector_retries_cached_unbound_pm_matches_when_pm_no_longer_returns_them():
    store = MemoryStore()
    pm_payload = [
        {
            **PM_EVENTS[0],
            "id": "394601",
            "slug": "lal-osa-bar-2026-05-02",
            "league": "La Liga 2025",
            "start_time": "2026-05-02T19:00:00Z",
            "home_team": "Ca Osasuna",
            "away_team": "Fc Barcelona",
            "volume": {"moneyline": 1_000_000, "total": 1_000_000},
        }
    ]
    asa_payload = [
        {
            "source": "asa",
            "match_id": "1608716",
            "pregame_id": "1608716",
            "inplay_id": "1608716",
            "league": "Primera",
            "home_team": "Osasuna",
            "away_team": "Barcelona",
            "start_time_utc": "2026-05-02T19:00:00Z",
            "status": "finished",
            "score": {"home": 1, "away": 2},
        }
    ]
    first_collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient(pm_payload),
        asa_client=StaticGSHttpClient(home=[], d1=[]),
        now=lambda: "2026-05-03T00:00:00Z",
    )
    first_collector.set_external_source("asa")
    first_report = await first_collector.collect_once()
    guid = first_report["pending_bindings"][0]["guid"]

    second_collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient([]),
        asa_client=StaticGSHttpClient(home=[], d1=asa_payload),
        now=lambda: "2026-05-03T00:05:00Z",
    )
    second_collector.set_external_source("asa")
    second_report = await second_collector.collect_once()

    assert second_report["pm_seen"] == 1
    assert second_report["matched"] == 1
    binding = await store.get_json(f"binding:{guid}")
    assert binding["status"] == "matched"
    assert binding["asa_match_id"] == "1608716"
    assert await store.get_text("idx:asa:id:1608716") == guid


@pytest.mark.asyncio
async def test_collector_lists_external_candidates_by_time_and_team_without_league_filter():
    store = MemoryStore()
    pm_payload = [
        {
            **PM_EVENTS[0],
            "id": "pm-manual",
            "slug": "lal-cel-elc-2026-05-03",
            "league": "La Liga 2025",
            "start_time": "2026-05-03T12:00:00Z",
            "home_team": "Celta Vigo",
            "away_team": "Elche",
            "volume": {"moneyline": 900000, "total": 900000},
        }
    ]
    asa_payload = [
        {
            "source": "asa",
            "match_id": "asa-close",
            "league": "Primera",
            "home_team": "Celta",
            "away_team": "Elche CF",
            "start_time_utc": "2026-05-03T14:00:00Z",
            "status": "live",
            "score": {"home": 1, "away": 0},
        },
        {
            "source": "asa",
            "match_id": "asa-other",
            "league": "Premier League",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "start_time_utc": "2026-05-03T14:00:00Z",
            "status": "live",
        },
    ]
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient(pm_payload),
        asa_client=StaticGSHttpClient(home=[], d1=asa_payload),
    )
    collector.set_external_source("asa")
    report = await collector.collect_once()
    guid = report["pending_bindings"][0]["guid"]

    candidates = await collector.external_candidates_for_match(guid, source="asa", limit=5)

    assert [row["external_match_id"] for row in candidates][:2] == ["asa-close", "asa-other"]
    assert candidates[0]["confidence"] > candidates[1]["confidence"]
    assert candidates[0]["league"] == "Primera"


@pytest.mark.asyncio
async def test_collector_manual_external_binding_writes_current_state_and_indices():
    store = MemoryStore()
    pm_payload = [
        {
            **PM_EVENTS[0],
            "id": "pm-manual",
            "slug": "lal-cel-elc-2026-05-03",
            "league": "La Liga 2025",
            "start_time": "2026-05-03T12:00:00Z",
            "home_team": "Celta Vigo",
            "away_team": "Elche",
            "volume": {"moneyline": 900000, "total": 900000},
        }
    ]
    asa_payload = [
        {
            "source": "asa",
            "match_id": "asa-close",
            "league": "Primera",
            "home_team": "Celta",
            "away_team": "Elche CF",
            "start_time_utc": "2026-05-03T14:00:00Z",
            "status": "live",
            "score": {"home": 1, "away": 0},
        }
    ]
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient(pm_payload),
        asa_client=StaticGSHttpClient(home=[], d1=asa_payload),
    )
    collector.set_external_source("asa")
    report = await collector.collect_once()
    guid = report["pending_bindings"][0]["guid"]

    binding = await collector.bind_external_match(guid, source="asa", external_match_id="asa-close")

    assert binding["status"] == "matched"
    assert binding["external_source"] == "asa"
    assert binding["external_match_id"] == "asa-close"
    assert await store.get_text("idx:asa:id:asa-close") == guid
    assert (await store.get_json(f"asa:match:{guid}"))["score_home"] == 1
    assert await store.get_text(f"idx:match:status:pending:{guid}") is None


@pytest.mark.asyncio
async def test_collector_preserves_realtime_fields_when_http_snapshot_lacks_them():
    store = MemoryStore()
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient(PM_EVENTS[:1]),
        gs_client=StaticGSHttpClient(home=GS_HOME, d1=GS_D1),
    )
    report = await collector.collect_once()
    guid = report["bindings"][0]["guid"]
    current = await store.get_json(f"pm:match:{guid}")
    current["status"] = "live"
    current["score_home"] = 2
    current["score_away"] = 3
    current["match_time"] = "2H 90"
    await store.set_json(f"pm:match:{guid}", current)

    await collector.collect_once()

    updated = await store.get_json(f"pm:match:{guid}")
    assert updated["match_time"] == "2H 90"
    assert updated["score_home"] == 2
    assert updated["score_away"] == 3


@pytest.mark.asyncio
async def test_collector_filters_by_volume_and_today_tomorrow_window():
    pm_payload = [
        {
            **PM_EVENTS[0],
            "id": "pm-good",
            "slug": "good-window",
            "start_time": "2026-05-01T19:00:00Z",
            "volume": {"total": 600000, "moneyline": 120000},
        },
        {
            **PM_EVENTS[1],
            "id": "pm-low-volume",
            "slug": "low-volume",
            "start_time": "2026-05-01T20:00:00Z",
            "volume": {"total": 499999, "moneyline": 400000},
        },
        {
            **PM_EVENTS[2],
            "id": "pm-too-late",
            "slug": "too-late",
            "start_time": "2026-05-03T01:00:00Z",
            "volume": {"total": 900000, "moneyline": 500000},
        },
    ]
    collector = Collector(
        store=MemoryStore(),
        pm_client=StaticPMHttpClient(pm_payload),
        gs_client=StaticGSHttpClient(home=[], d1=[]),
        now=lambda: "2026-05-01T00:00:00Z",
    )
    collector.set_filters(football_volume_threshold_k=500, upcoming_days=2)

    report = await collector.collect_once()

    assert report["pm_seen"] == 1
    assert report["pending"] == 1
    assert report["pending_bindings"][0]["pm_slug"] == "good-window"


@pytest.mark.asyncio
async def test_collector_keeps_live_match_that_started_yesterday_after_midnight():
    pm_payload = [
        {
            **PM_EVENTS[0],
            "id": "pm-live-yesterday",
            "slug": "epl-mun-liv-2026-05-03",
            "start_time": "2026-05-03T14:30:00Z",
            "status": "live",
            "volume": {"total": 8_000_000, "moneyline": 8_000_000},
        }
    ]
    collector = Collector(
        store=MemoryStore(),
        pm_client=StaticPMHttpClient(pm_payload),
        gs_client=StaticGSHttpClient(home=[], d1=[]),
        now=lambda: "2026-05-03T16:10:00Z",
    )
    collector.set_filters(football_volume_threshold_k=500, upcoming_days=2)

    report = await collector.collect_once()

    assert report["pm_seen"] == 1
    assert report["pending_bindings"][0]["pm_slug"] == "epl-mun-liv-2026-05-03"


@pytest.mark.asyncio
async def test_collector_stops_collecting_finished_match_after_15_minutes():
    pm_payload = [
        {
            **PM_EVENTS[0],
            "id": "pm-finished-old",
            "slug": "finished-old",
            "start_time": "2026-05-01T19:00:00Z",
            "status": "finished",
            "volume": {"total": 900000, "moneyline": 500000},
            "updated_at_utc": "2026-05-01T21:00:00Z",
        },
        {
            **PM_EVENTS[1],
            "id": "pm-finished-recent",
            "slug": "finished-recent",
            "start_time": "2026-05-01T19:00:00Z",
            "status": "finished",
            "volume": {"total": 900000, "moneyline": 500000},
            "updated_at_utc": "2026-05-01T21:06:00Z",
        },
    ]
    collector = Collector(
        store=MemoryStore(),
        pm_client=StaticPMHttpClient(pm_payload),
        gs_client=StaticGSHttpClient(home=[], d1=[]),
        now=lambda: "2026-05-01T21:20:00Z",
    )
    collector.set_filters(football_volume_threshold_k=500, upcoming_days=2)

    report = await collector.collect_once()

    assert report["pm_seen"] == 1
    assert report["pending_bindings"][0]["pm_slug"] == "finished-recent"


@pytest.mark.asyncio
async def test_collector_enqueues_pm_snapshot_events_for_trader():
    class RecordingTrader:
        def __init__(self):
            self.events = []

        def enqueue_event(self, event):
            self.events.append(event)

    trader = RecordingTrader()
    collector = Collector(
        store=MemoryStore(),
        pm_client=StaticPMHttpClient(PM_EVENTS[:1]),
        gs_client=StaticGSHttpClient(home=[], d1=[]),
        trader_manager=trader,
        now=lambda: "2026-05-01T00:00:00Z",
    )

    report = await collector.collect_once()

    assert trader.events == [
        {
            "guid": report["pending_bindings"][0]["guid"],
            "source": "pm_http",
            "received_at_utc": report["completed_at_utc"],
        }
    ]


@pytest.mark.asyncio
async def test_collector_start_runs_continuous_loop_until_stopped():
    store = MemoryStore()
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient(PM_EVENTS[:1]),
        gs_client=StaticGSHttpClient(home=GS_HOME, d1=GS_D1),
        interval_seconds=0.01,
    )

    await collector.start()
    try:
        await _wait_until(lambda: collector.status().collector_last_success_at is not None)
    finally:
        await collector.stop()

    status = collector.status()
    assert status.collector_running is False
    assert status.collector_last_error is None
    assert status.collector_next_run_at is not None
    assert status.matches_count == 1
    assert await store.get_json("collector:last_report") is not None


async def _wait_until(predicate, timeout_seconds: float = 0.5) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")
