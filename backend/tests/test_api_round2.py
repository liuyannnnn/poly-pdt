from fastapi.testclient import TestClient

from app.collector import Collector, StaticGSHttpClient, StaticPMHttpClient
from app.main import create_app
from app.store import MemoryStore

from .fixtures import GS_D1, GS_HOME, PM_EVENTS


def test_api_returns_collected_matches_and_trader_state():
    store = MemoryStore()
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient(PM_EVENTS[:1]),
        gs_client=StaticGSHttpClient(home=GS_HOME, d1=GS_D1),
    )
    collector.set_external_source("gs")
    app = create_app(
        store=store,
        collector=collector,
    )

    with TestClient(app) as client:
        collect = client.post("/api/v1/collector/run")
        matches = client.get("/api/v1/matches")
        catalog = client.get("/api/v1/strategies/catalog")
        created = client.post(
            "/api/v1/tradings",
            json={
                "strategy_name": "football_score_delay_trade",
                "strategy_params": {"initial_balance": 1000, "stake_usd": 100},
                "affect_sports": ["football"],
                "mode": "simulation",
            },
        )
        accounts = client.get("/api/v1/accounts")
        invalid = client.post(
            "/api/v1/tradings",
            json={
                "strategy_name": "football_score_delay_trade",
                "strategy_params": {},
                "affect_sports": ["basketball"],
                "mode": "simulation",
            },
        )

    assert collect.status_code == 200
    assert collect.json()["matched"] >= 1
    assert matches.status_code == 200
    assert matches.json()[0]["sport"] == "football"
    assert matches.json()[0]["match_id"].startswith("guid-")
    assert catalog.status_code == 200
    assert catalog.json()[0]["key"] == "football_score_delay_trade"
    assert created.status_code == 200
    assert accounts.json()[0]["available_cash"] == 1000
    assert invalid.status_code == 400
