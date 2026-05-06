from fastapi.testclient import TestClient

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.collector import Collector, StaticGSHttpClient, StaticPMHttpClient
from app.config import Settings
from app.api import _passes_current_match_filter
from app.main import create_app
from app.models import CollectorStatus
from app.store import MemoryStore


def test_health_endpoint_returns_runtime_and_redis_state():
    store = MemoryStore()
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient([]),
        gs_client=StaticGSHttpClient(home=[], d1=[]),
    )
    isolated_app = create_app(store=store, collector=collector)

    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["runtime"]["running"] is True
    assert "redis" in payload
    assert "connected" in payload["redis"]
    assert "url" not in payload["redis"]


def test_empty_api_endpoints_return_frontend_safe_payloads():
    isolated_app = create_app(store=MemoryStore())
    with TestClient(isolated_app) as client:
        for path in [
            "/api/v1/matches",
            "/api/v1/matches/history",
            "/api/v1/ticks?match_id=missing&limit=10",
            "/api/v1/matches/missing/snapshots",
            "/api/v1/accounts",
            "/api/v1/positions",
            "/api/v1/trades",
            "/api/v1/logs",
            "/api/v1/tradings",
            "/api/v1/pm/accounts",
        ]:
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.json() == []


def test_match_snapshots_endpoint_returns_all_and_live_series_rows():
    store = MemoryStore()

    async def seed() -> None:
        await store.set_json(
            "pm:match:guid-match-1",
            {
                "guid": "guid-match-1",
                "sport": "football",
                "league": "Premier League",
                "home_team": "Brentford FC",
                "away_team": "West Ham United FC",
                "start_time_utc": "2026-05-02T14:00:00Z",
                "status": "live",
                "updated_at_utc": "2026-05-02T14:10:00Z",
                "score_home": 0,
                "score_away": 0,
                "pm_event_id": "394220",
                "slug": "epl-bre-wes-2026-05-02",
                "condition_id": "cond",
                "home_bid1": 0.52,
                "home_ask1": 0.53,
                "draw_bid1": 0.23,
                "draw_ask1": 0.24,
                "away_bid1": 0.24,
                "away_ask1": 0.25,
                "moneyline_volume": 681362.91,
                "total_volume": 681362.91,
                "raw": {"game_id": 90091343},
            },
        )
        await store.set_json(
            "series:pm:collector:guid-match-1",
            [
                {
                    "match_id": "guid-match-1",
                    "snapshot_ts_utc": "2026-05-02T14:00:00Z",
                    "source": "pm_http",
                    "phase": "ALL",
                    "home_ask": 0.53,
                    "draw_ask": 0.24,
                    "away_ask": 0.25,
                }
            ],
        )
        await store.set_json(
            "series:pm:10s:guid-match-1",
            [
                {
                    "match_id": "guid-match-1",
                    "snapshot_ts_utc": "2026-05-02T14:00:10Z",
                    "source": "pm_market",
                    "phase": "LIVE",
                    "home_ask": 0.54,
                    "draw_ask": 0.24,
                    "away_ask": 0.25,
                }
            ],
        )

    asyncio.run(seed())
    isolated_app = create_app(store=store, collector=Collector(store=store, pm_client=StaticPMHttpClient([])))

    with TestClient(isolated_app) as client:
        both = client.get("/api/v1/matches/guid-match-1/snapshots")
        live = client.get("/api/v1/matches/guid-match-1/snapshots?series=live")

    assert both.status_code == 200
    assert [row["phase"] for row in both.json()] == ["ALL", "LIVE"]
    assert both.json()[0]["team_home"] == "Brentford FC"
    assert both.json()[0]["game_id"] == 90091343
    assert live.json()[0]["phase"] == "LIVE"


def test_trades_and_logs_endpoints_ignore_legacy_json_lists():
    store = MemoryStore()

    async def seed() -> None:
        await store.set_json(
            "trader:S001:trades",
            [{"trading_id": "S001", "guid": "legacy-guid", "side": "buy", "ts_utc": "2026-05-01T00:00:00Z"}],
        )
        await store.set_json(
            "trader:S001:logs",
            [{"trader_id": "S001", "guid": "legacy-guid", "message": "legacy", "ts_utc": "2026-05-01T00:00:00Z"}],
        )

    asyncio.run(seed())
    isolated_app = create_app(store=store, collector=Collector(store=store, pm_client=StaticPMHttpClient([])))

    with TestClient(isolated_app) as client:
        trades = client.get("/api/v1/trades")
        logs = client.get("/api/v1/logs")

    assert trades.status_code == 200
    assert logs.status_code == 200
    assert trades.json() == []
    assert logs.json() == []


def test_matches_endpoint_uses_external_update_time_when_it_is_newer_than_pm():
    store = MemoryStore()

    async def seed() -> None:
        await store.set_json(
            "pm:match:guid-match-1",
            {
                "guid": "guid-match-1",
                "sport": "football",
                "league": "Premier League",
                "home_team": "Manchester United Fc",
                "away_team": "Liverpool Fc",
                "start_time_utc": "2026-05-03T14:30:00Z",
                "status": "live",
                "updated_at_utc": "2026-05-03T15:53:58Z",
                "score_home": 2,
                "score_away": 2,
                "pm_event_id": "397942",
                "slug": "epl-mun-liv-2026-05-03",
                "condition_id": "cond",
                "moneyline_volume": 1_000_000,
                "total_volume": 1_000_000,
            },
        )
        await store.set_json(
            "binding:guid-match-1",
            {
                "guid": "guid-match-1",
                "external_source": "asa",
                "external_match_id": "1626475",
                "status": "matched",
            },
        )
        await store.set_json(
            "external:match:guid-match-1",
            {
                "source": "asa",
                "updated_at_utc": "2026-05-03T15:54:41.432764Z",
                "shots_on_target": {"home": 3, "away": 4},
            },
        )

    asyncio.run(seed())
    isolated_app = create_app(store=store, collector=Collector(store=store, pm_client=StaticPMHttpClient([])))

    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/matches")

    assert response.status_code == 200
    assert response.json()[0]["latest_ts_utc"] == "2026-05-03T15:54:41.432764Z"


def test_history_matches_return_newest_finished_matches_first():
    store = MemoryStore()

    async def seed() -> None:
        await store.set_json(
            "pm:match:guid-old",
            {
                "guid": "guid-old",
                "sport": "football",
                "league": "Premier League",
                "home_team": "Old Home",
                "away_team": "Old Away",
                "start_time_utc": "2026-05-04T14:00:00Z",
                "status": "finished",
                "updated_at_utc": "2026-05-04T16:00:00Z",
                "pm_event_id": "old",
                "slug": "old-finished",
            },
        )
        await store.set_json(
            "pm:match:guid-new",
            {
                "guid": "guid-new",
                "sport": "football",
                "league": "Premier League",
                "home_team": "New Home",
                "away_team": "New Away",
                "start_time_utc": "2026-05-04T19:00:00Z",
                "status": "finished",
                "updated_at_utc": "2026-05-04T21:00:00Z",
                "pm_event_id": "new",
                "slug": "new-finished",
            },
        )

    asyncio.run(seed())
    isolated_app = create_app(store=store, collector=Collector(store=store, pm_client=StaticPMHttpClient([])))

    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/matches/history?limit=1")

    assert response.status_code == 200
    assert response.json()[0]["match_id"] == "guid-new"


def test_active_matches_return_newest_start_time_first():
    store = MemoryStore()

    async def seed() -> None:
        await store.set_json(
            "pm:match:guid-earlier",
            {
                "guid": "guid-earlier",
                "sport": "football",
                "league": "Premier League",
                "home_team": "Earlier Home",
                "away_team": "Earlier Away",
                "start_time_utc": "2026-05-05T10:00:00Z",
                "status": "scheduled",
                "updated_at_utc": "2026-05-05T09:00:00Z",
                "pm_event_id": "earlier",
                "slug": "earlier-active",
            },
        )
        await store.set_json(
            "pm:match:guid-later",
            {
                "guid": "guid-later",
                "sport": "football",
                "league": "Premier League",
                "home_team": "Later Home",
                "away_team": "Later Away",
                "start_time_utc": "2026-05-05T12:00:00Z",
                "status": "scheduled",
                "updated_at_utc": "2026-05-05T09:00:00Z",
                "pm_event_id": "later",
                "slug": "later-active",
            },
        )

    asyncio.run(seed())
    isolated_app = create_app(store=store, collector=Collector(store=store, pm_client=StaticPMHttpClient([])))

    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/matches")

    assert response.status_code == 200
    assert [row["match_id"] for row in response.json()] == ["guid-later", "guid-earlier"]


def test_strategy_catalog_exposes_second_round_strategy():
    isolated_app = create_app(store=MemoryStore())
    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/strategies/catalog")

    assert response.status_code == 200
    assert response.json()[0]["key"] == "football_score_delay_trade"
    assert response.json()[0]["params"] == []
    assert response.json()[1]["key"] == "football_winrate_gap_buy"
    assert response.json()[1]["display_name"] == "胜率差买入"
    assert [param["key"] for param in response.json()[1]["params"]] == [
        "winrate_gap_pct",
        "entry_before_minutes",
        "entry_after_minutes",
    ]


def test_logs_endpoint_returns_stored_match_process_stream_rows_without_filtering_history():
    store = MemoryStore()

    async def seed() -> None:
        await store.add_stream(
            "stream:match_logs",
            {
                "ts_utc": "2026-05-02T14:30:12Z",
                "guid": "guid-match-1",
                "source": "discriminator",
                "data_source": "gs_ws",
                "event_kind": "score_changed",
                "message": "比分变化 0-0 -> 1-0",
            },
        )
        await store.add_stream(
            "stream:match_logs",
            {
                "ts_utc": "2026-05-02T14:31:12Z",
                "guid": "guid-match-1",
                "source": "discriminator",
                "data_source": "asa_live",
                "event_kind": "shots_on_target_changed",
                "message": "射正 3-1",
            },
        )

    asyncio.run(seed())
    isolated_app = create_app(store=store)

    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/logs?match_id=guid-match-1")

    assert response.status_code == 200
    assert response.json() == [
        {
            "ts_utc": "2026-05-02T14:30:12Z",
            "guid": "guid-match-1",
            "source": "discriminator",
            "data_source": "gs_ws",
            "event_kind": "score_changed",
            "message": "比分变化 0-0 -> 1-0",
        },
        {
            "ts_utc": "2026-05-02T14:31:12Z",
            "guid": "guid-match-1",
            "source": "discriminator",
            "data_source": "asa_live",
            "event_kind": "shots_on_target_changed",
            "message": "射正 3-1",
        },
    ]


def test_external_source_match_includes_provider_widget_url():
    store = MemoryStore()

    async def seed() -> None:
        await store.set_json(
            "asa:match:guid-match-1",
            {
                "source": "asa",
                "guid": "guid-match-1",
                "match_id": "asa-100",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
            },
        )

    asyncio.run(seed())
    isolated_app = create_app(store=store)

    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/external-source/match/guid-match-1")

    assert response.status_code == 200
    assert response.json()["widget_source"] == "asa"
    assert response.json()["widget_url"].startswith("https://allsportsapi.com/widgets/")


def test_collector_settings_can_be_read_and_updated_without_trd():
    payload = {
        "collection_interval_minutes": 3,
        "football_volume_threshold_k": 600,
        "external_source": "gs",
    }
    store = MemoryStore()
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient([]),
        gs_client=StaticGSHttpClient(home=[], d1=[]),
    )
    isolated_app = create_app(store=store, collector=collector)

    with TestClient(isolated_app) as client:
        default_response = client.get("/api/v1/settings/collector")
        update_response = client.put("/api/v1/settings/collector", json=payload)

    assert default_response.status_code == 200
    assert default_response.json()["external_source"] == "asa"
    assert "basketball_volume_threshold_k" not in default_response.json()
    assert update_response.status_code == 200
    assert update_response.json() == payload


def test_collector_settings_save_triggers_immediate_collection():
    class CountingCollector:
        def __init__(self):
            self.collect_count = 0
            self.filters = None

        async def start(self):
            return None

        async def stop(self):
            return None

        def set_interval_minutes(self, minutes: int):
            self.interval_minutes = minutes

        def set_filters(self, **kwargs):
            self.filters = kwargs

        def status(self):
            return CollectorStatus()

        async def collect_once(self):
            self.collect_count += 1
            return {"pm_seen": 1}

    collector = CountingCollector()
    isolated_app = create_app(settings=Settings(), store=MemoryStore(), collector=collector)

    with TestClient(isolated_app) as client:
        response = client.put(
            "/api/v1/settings/collector",
            json={
                "collection_interval_minutes": 3,
                "football_volume_threshold_k": 100,
                "external_source": "gs",
            },
        )

    assert response.status_code == 200
    assert collector.filters == {"football_volume_threshold_k": 100, "upcoming_days": 2}
    assert collector.collect_count == 1


def test_collector_settings_reject_trd_source():
    isolated_app = create_app(store=MemoryStore())
    with TestClient(isolated_app) as client:
        response = client.put(
            "/api/v1/settings/collector",
            json={
                "collection_interval_minutes": 3,
                "football_volume_threshold_k": 600,
                "external_source": "trd",
            },
        )

    assert response.status_code == 422


def test_collector_status_reports_runtime_and_disconnected_streams():
    isolated_app = create_app(store=MemoryStore())
    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/collector/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["collector_running"] is True
    assert payload["external_stream_started"] is False
    assert payload["pm_market_ws_connected"] is False
    assert payload["pm_user_ws_connected"] is False
    assert payload["pm_sports_ws_connected"] is False
    assert payload["gs_ws_connected"] is False
    assert payload["polymarket_ws_connected"] is False
    assert payload["goalserve_connected"] is False
    assert "trd_connected" not in payload
    assert payload["matches_count"] == 0


def test_current_match_filter_keeps_live_match_that_started_yesterday_after_midnight():
    assert _passes_current_match_filter(
        {
            "total_volume": 8_000_000,
            "start_time_utc": "2026-05-03T14:30:00Z",
            "status": "live",
        },
        collector_settings=type("Settings", (), {"football_volume_threshold_k": 500})(),
        timezone_name="Asia/Shanghai",
        now_utc="2026-05-03T16:10:00Z",
    )


def test_trading_lifecycle_endpoints_are_skeleton_only():
    create_payload = {
        "strategy_name": "football_score_delay_trade",
        "strategy_params": {},
        "affect_sports": ["football"],
        "mode": "simulation",
    }

    isolated_app = create_app(store=MemoryStore())
    with TestClient(isolated_app) as client:
        created = client.post("/api/v1/tradings", json=create_payload)
        trading_id = created.json()["trading_id"]
        started = client.post(f"/api/v1/tradings/{trading_id}/start")
        stopped = client.post(f"/api/v1/tradings/{trading_id}/stop")
        updated = client.put(f"/api/v1/tradings/{trading_id}", json={"strategy_params": {"x": 1}})
        deleted = client.delete(f"/api/v1/tradings/{trading_id}")
        missing_start = client.post("/api/v1/tradings/S999/start")
        missing_update = client.put("/api/v1/tradings/S999", json={"strategy_params": {"x": 1}})
        sim_started = client.post(
            "/api/v1/simulation/start",
            json={"initial_balance": 10000, "retracement": 0.05},
        )
        sim_stopped = client.post("/api/v1/simulation/stop")

    assert created.status_code == 200
    assert created.json()["status"] == "stopped"
    assert started.json()["status"] == "running"
    assert stopped.json()["status"] == "stopped"
    assert updated.json()["status"] == "stopped"
    assert deleted.json() == {"deleted": True}
    assert missing_start.status_code == 404
    assert missing_update.status_code == 404
    assert sim_started.json() == {"running": True}
    assert sim_stopped.json() == {"running": False}


def test_pm_accounts_endpoint_returns_only_redacted_configured_accounts():
    settings = Settings(
        pm_accounts_json=(
            '[{"alias":"pm-main","label":"Main PM","host":"https://clob.polymarket.com",'
            '"chain_id":137,"private_key":"secret-private-key","funder":"0x123",'
            '"signature_type":1,"api_key":"secret-key","api_secret":"secret-secret",'
            '"api_passphrase":"secret-pass","relayer_api_key":"secret-relayer",'
            '"relayer_address":"0x456","live_trading_enabled":false}]'
        )
    )
    isolated_app = create_app(settings=settings, store=MemoryStore())

    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/pm/accounts")

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "id": "pm-main",
            "name": "Main PM",
            "host": "https://clob.polymarket.com",
            "chain_id": 137,
            "funder_configured": True,
            "private_key_configured": True,
            "api_credentials_configured": True,
            "relayer_configured": True,
            "live_trading_enabled": False,
            "total_funds": 0.0,
            "position_funds": 0.0,
            "available_funds": 0.0,
        }
    ]
    assert "secret-private-key" not in response.text
    assert "secret-secret" not in response.text
    assert "secret-relayer" not in response.text


def test_trades_endpoint_enriches_stream_rows_with_match_slug():
    store = MemoryStore()

    async def seed() -> None:
        await store.set_json(
            "pm:match:guid-match-1",
            {
                "guid": "guid-match-1",
                "slug": "epl-bou-cry-2026-05-03",
                "home_team": "Afc Bournemouth",
                "away_team": "Crystal Palace Fc",
            },
        )
        await store.add_stream(
            "stream:trader:S001:trades",
            {
                "trading_id": "S001",
                "side": "buy",
                "guid": "guid-match-1",
                "outcome_key": "home",
                "shares": 100,
                "price": 0.2,
                "ts_utc": "2026-05-03T13:08:09Z",
            },
        )

    asyncio.run(seed())
    isolated_app = create_app(store=store)

    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/trades?match_id=guid-match-1")

    assert response.status_code == 200
    row = response.json()[0]
    assert row["slug"] == "epl-bou-cry-2026-05-03"
    assert row["team_label"] == "Home"
    assert row["team_name"] == "Afc Bournemouth"


def test_trades_endpoint_does_not_mix_stale_store_rows_after_trader_hydration():
    store = MemoryStore()

    async def seed() -> None:
        await store.set_json(
            "trader:S011:config",
            {
                "trading_id": "S011",
                "status": "running",
                "mode": "simulation",
                "strategy_name": "football_score_delay_trade",
                "strategy_params": {},
                "affect_sports": ["football"],
            },
        )
        await store.set_json("trader:S011:state", {"status": "running"})
        await store.set_json(
            "trader:S011:account",
            {
                "trading_id": "S011",
                "mode": "simulation",
                "account_alias": None,
                "initial_balance": 10000.0,
                "available_cash": 10000.0,
            },
        )
        await store.set_json("trader:S011:trades", [])
        await store.set_json(
            "trader:S001:trades",
            [
                {
                    "trading_id": "S001",
                    "side": "buy",
                    "guid": "guid-old",
                    "outcome_key": "home",
                    "shares": 100,
                    "price": 0.2,
                    "ts_utc": "2026-05-03T13:08:09Z",
                }
            ],
        )

    asyncio.run(seed())
    isolated_app = create_app(store=store)

    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/trades")

    assert response.status_code == 200
    assert response.json() == []


def test_accounts_endpoint_reports_current_calendar_day_realized_profit():
    store = MemoryStore()
    zone = ZoneInfo("Asia/Shanghai")
    local_noon = datetime.now(zone).replace(hour=12, minute=0, second=0, microsecond=0)
    today_ts = local_noon.astimezone(UTC).isoformat().replace("+00:00", "Z")
    yesterday_ts = (local_noon - timedelta(days=1)).astimezone(UTC).isoformat().replace("+00:00", "Z")

    async def seed() -> None:
        await store.set_json(
            "trader:S021:config",
            {
                "trading_id": "S021",
                "status": "running",
                "mode": "simulation",
                "strategy_name": "football_score_delay_trade",
                "strategy_params": {},
                "affect_sports": ["football"],
            },
        )
        await store.set_json("trader:S021:state", {"status": "running"})
        await store.set_json(
            "trader:S021:account",
            {
                "trading_id": "S021",
                "mode": "simulation",
                "account_alias": None,
                "initial_balance": 1000.0,
                "available_cash": 1000.0,
            },
        )
        await store.add_stream(
            "stream:trader:S021:trades",
            {"trading_id": "S021", "side": "sell", "profit": 12.34, "ts_utc": today_ts},
            max_len=None,
        )
        await store.add_stream(
            "stream:trader:S021:trades",
            {"trading_id": "S021", "side": "buy", "profit": 99.0, "ts_utc": today_ts},
            max_len=None,
        )
        await store.add_stream(
            "stream:trader:S021:trades",
            {"trading_id": "S021", "side": "sell", "profit": 6.0, "ts_utc": yesterday_ts},
            max_len=None,
        )
        await store.add_stream(
            "stream:trader:S021:trades",
            {"trading_id": "S021", "side": "sell", "profit": -2.0, "ts_utc": today_ts},
            max_len=None,
        )

    asyncio.run(seed())
    isolated_app = create_app(store=store)

    with TestClient(isolated_app) as client:
        response = client.get("/api/v1/accounts")

    assert response.status_code == 200
    row = response.json()[0]
    assert row["id"] == "S021"
    assert row["today_profit"] == 10.34
    assert row["win_rate"] == 0.6667


def test_real_trading_requires_configured_pm_account_alias():
    settings = Settings(
        pm_accounts_json='[{"alias":"pm-main","label":"Main PM","private_key":"x","api_key":"k","api_secret":"s","api_passphrase":"p"}]'
    )
    isolated_app = create_app(settings=settings, store=MemoryStore())
    payload = {
        "strategy_name": "football_score_delay_trade",
        "strategy_params": {"real_dry_run": True},
        "affect_sports": ["football"],
        "mode": "real",
    }

    with TestClient(isolated_app) as client:
        missing = client.post("/api/v1/tradings", json=payload)
        valid = client.post("/api/v1/tradings", json={**payload, "account_alias": "pm-main"})

    assert missing.status_code == 400
    assert valid.status_code == 200
    assert valid.json()["mode"] == "real"


def test_real_trading_display_rows_use_pm_account_source(monkeypatch):
    async def fake_pm_accounts(settings: Settings) -> list[dict[str, Any]]:
        return [
            {
                "id": "pm-main",
                "name": "Main PM",
                "host": "https://clob.polymarket.com",
                "chain_id": 137,
                "funder_configured": True,
                "private_key_configured": True,
                "api_credentials_configured": True,
                "relayer_configured": True,
                "live_trading_enabled": True,
                "total_funds": 12.34,
                "position_funds": 2.34,
                "available_funds": 10.0,
            }
        ]

    async def fake_pm_positions(settings: Settings, account_alias: str | None = None) -> list[dict[str, Any]]:
        assert account_alias == "pm-main"
        return [
            {
                "account_alias": "pm-main",
                "order_id": "pm-position-1",
                "slug": "epl-demo-2026-05-04",
                "outcome_key": "home",
                "team_label": "Home",
                "team_name": "Demo FC",
                "shares": 3,
                "avg_entry_price": 0.5,
                "cost_basis": 1.5,
                "current_bid1": 0.6,
                "unrealized_pnl": 0.3,
            }
        ]

    async def fake_pm_trades(settings: Settings, account_alias: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        assert account_alias == "pm-main"
        return [
            {
                "account_alias": "pm-main",
                "order_id": "pm-trade-1",
                "side": "buy",
                "slug": "epl-demo-2026-05-04",
                "outcome_key": "home",
                "team_label": "Home",
                "team_name": "Demo FC",
                "shares": 3,
                "price": 0.5,
                "amount_usd": 1.5,
                "ts_utc": "2026-05-04T01:02:03Z",
            }
        ]

    monkeypatch.setattr("app.api.public_pm_accounts_with_balances", fake_pm_accounts)
    monkeypatch.setattr("app.api.public_pm_positions", fake_pm_positions)
    monkeypatch.setattr("app.api.public_pm_trades", fake_pm_trades)

    settings = Settings(
        pm_accounts_json='[{"alias":"pm-main","label":"Main PM","private_key":"x","api_key":"k","api_secret":"s","api_passphrase":"p"}]'
    )
    isolated_app = create_app(settings=settings, store=MemoryStore())
    payload = {
        "strategy_name": "football_score_delay_trade",
        "strategy_params": {"real_dry_run": True},
        "affect_sports": ["football"],
        "mode": "real",
        "account_alias": "pm-main",
    }

    with TestClient(isolated_app) as client:
        created = client.post("/api/v1/tradings", json=payload).json()
        accounts = client.get("/api/v1/accounts").json()
        positions = client.get("/api/v1/positions").json()
        trades = client.get(f"/api/v1/trades?trading_id={created['trading_id']}").json()

    assert accounts[0]["total_assets"] == 12.34
    assert accounts[0]["available_cash"] == 10.0
    assert accounts[0]["position_count"] == 1
    assert positions[0]["trading_id"] == created["trading_id"]
    assert positions[0]["source"] == "pm"
    assert trades[0]["trading_id"] == created["trading_id"]
    assert trades[0]["source"] == "pm"


def test_market_websocket_sends_heartbeat_first():
    isolated_app = create_app(store=MemoryStore())
    with TestClient(isolated_app) as client:
        with client.websocket_connect("/api/v1/ws/market") as websocket:
            message = websocket.receive_json()

    assert message["type"] == "heartbeat"
    assert message["source"] == "pdt2.1"
    assert "ts_utc" in message
