import pytest
from fastapi.testclient import TestClient

from app.collector import Collector, StaticGSHttpClient, StaticPMHttpClient
from app.config import Settings
from app.connectivity import ConnectivityChecker, StaticConnectivityTransport
from app.listener import BroadcastHub, Listener
from app.main import create_app
from app.retention import cleanup_retention
from app.soak import DryRunSoakRunner
from app.store import MemoryStore
from app.trader import TraderManager

from .fixtures import GS_D1, GS_HOME, PM_EVENTS


def build_fixture_stack():
    store = MemoryStore()
    hub = BroadcastHub()
    trader = TraderManager(store=store)
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient(PM_EVENTS[:1]),
        gs_client=StaticGSHttpClient(home=GS_HOME, d1=GS_D1),
    )
    listener = Listener(store=store, broadcaster=hub, trader_manager=trader)
    return store, hub, trader, collector, listener


@pytest.mark.asyncio
async def test_retention_cleanup_and_store_redaction_remove_sensitive_values():
    store = MemoryStore()
    await store.set_json(
        "pm:raw:guid-1:sensitive",
        {
            "private_key": "SENSITIVE_VALUE",
            "api_secret": "SENSITIVE_VALUE",
            "nested": {"token": "SENSITIVE_VALUE", "safe": "kept"},
        },
        ttl_seconds=600,
    )
    await store.set_json("orderbook:guid-1:home", {"ask1": 0.5}, ttl_seconds=600)
    await store.set_json("trader:t1:trades", [{"order_id": "o1"}])
    store.force_expire("orderbook:guid-1:home")

    deleted = await cleanup_retention(store)
    redacted = await store.get_json("pm:raw:guid-1:sensitive")

    assert deleted == ["orderbook:guid-1:home"]
    assert redacted["private_key"] == "[REDACTED]"
    assert redacted["api_secret"] == "[REDACTED]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "kept"
    assert await store.get_json("trader:t1:trades") == [{"order_id": "o1"}]


@pytest.mark.asyncio
async def test_store_stream_limit_returns_latest_rows_only():
    store = MemoryStore()
    for index in range(5):
        await store.add_stream("stream:events", {"index": index})

    assert await store.stream("stream:events", limit=2) == [{"index": 3}, {"index": 4}]


@pytest.mark.asyncio
async def test_store_stream_max_len_retains_latest_rows_only():
    store = MemoryStore()
    for index in range(5):
        await store.add_stream("stream:events", {"index": index}, max_len=3)

    assert await store.stream("stream:events") == [{"index": 2}, {"index": 3}, {"index": 4}]


@pytest.mark.asyncio
async def test_pm_user_reconciliation_updates_account_orders_fills_without_submission():
    store, hub, trader, collector, listener = build_fixture_stack()
    await collector.collect_once()

    event = await listener.process_payload(
        "pm_user",
        {
            "account_alias": "pm-main",
            "balance": 1000,
            "available_cash": 950,
            "orders": [{"order_id": "ord-1", "status": "open", "asset_id": "asset-ars-home"}],
            "fills": [{"fill_id": "fill-1", "order_id": "ord-1", "price": 0.52, "size": 10}],
            "api_secret": "SENSITIVE_VALUE",
            "message_id": "pm-user-1",
        },
    )

    account = await store.get_json("account:pm-main")
    orders = await store.stream("stream:orders")
    fills = await store.stream("stream:fills")
    broadcasts = await hub.drain()

    assert event["source"] == "pm_user"
    assert event["changed_fields"] == ["account", "orders", "fills"]
    assert account["available_cash"] == 950
    assert account.get("api_secret") is None
    assert orders[-1]["order_id"] == "ord-1"
    assert fills[-1]["fill_id"] == "fill-1"
    assert broadcasts[-1]["topic"] == "account.update"
    assert "SENSITIVE_VALUE" not in str(await store.stream("stream:standard_events"))


@pytest.mark.asyncio
async def test_connectivity_checker_reports_pm_and_gs_channels_without_secrets():
    checker = ConnectivityChecker(
        settings=Settings(
            pm_user_ws_url="wss://ws-subscriptions-clob.polymarket.test/ws/user",
            gs_http_url="https://goalserve.test/soccer",
            gs_ws_url="wss://goalserve.test/ws/soccer",
        ),
        transport=StaticConnectivityTransport(
            {
                "pm_http": True,
                "pm_sports_ws": True,
                "pm_market_ws": True,
                "pm_user_ws": False,
                "gs_http": True,
                "gs_ws": False,
            }
        )
    )

    result = await checker.check_all()

    assert set(result["checks"]) == {
        "pm_http",
        "pm_sports_ws",
        "pm_market_ws",
        "pm_user_ws",
        "gs_http",
        "gs_ws",
    }
    assert result["checks"]["pm_http"]["ok"] is True
    assert result["checks"]["pm_user_ws"]["ok"] is False
    assert "secret" not in str(result).lower()
    assert "token" not in str(result).lower()


@pytest.mark.asyncio
async def test_connectivity_checker_keeps_real_account_ws_unconfigured_by_default():
    checker = ConnectivityChecker(
        settings=Settings(),
        transport=StaticConnectivityTransport({"pm_http": True}),
    )

    result = await checker.check_all()

    assert result["checks"]["pm_http"]["configured"] is True
    assert result["checks"]["pm_user_ws"]["configured"] is False
    assert result["checks"]["pm_user_ws"]["detail"] == "not_configured"


@pytest.mark.asyncio
async def test_dry_run_soak_runs_collector_listener_trader_and_persists_api_state():
    store, hub, trader, collector, listener = build_fixture_stack()
    runner = DryRunSoakRunner(
        store=store,
        collector=collector,
        listener=listener,
        trader_manager=trader,
    )

    report = await runner.run(iterations=2, cleanup_trader=False)

    assert report["matched"] == 1
    assert report["events_processed"] == 1
    assert report["trades"] >= 1
    assert report["real_submissions"] == 0
    assert report["dry_run"] is True

    app = create_app(
        store=store,
        collector=collector,
        listener=listener,
        trader=trader,
        broadcaster=hub,
        connectivity_checker=ConnectivityChecker(transport=StaticConnectivityTransport({})),
        soak_runner=runner,
    )
    with TestClient(app) as client:
        accounts = client.get("/api/v1/accounts")
        positions = client.get("/api/v1/positions")
        trades = client.get("/api/v1/trades")
        logs = client.get("/api/v1/logs")
        soak = client.post("/api/v1/soak/dry-run")
        accounts_after_soak = client.get("/api/v1/accounts")

    assert accounts.json()[0]["available_cash"] < accounts.json()[0]["initial_balance"]
    assert positions.json()[0]["guid"].startswith("guid-")
    assert trades.json()[0]["side"] == "buy"
    assert any(row.get("guid", "").startswith("guid-") for row in logs.json())
    assert any(row.get("trader_id", "").startswith("S") for row in logs.json())
    assert soak.json()["real_submissions"] == 0
    assert len(accounts_after_soak.json()) == len(accounts.json())


def test_connectivity_endpoint_uses_injected_checker():
    app = create_app(
        store=MemoryStore(),
        connectivity_checker=ConnectivityChecker(
            settings=Settings(gs_http_url="https://goalserve.test/soccer"),
            transport=StaticConnectivityTransport({"pm_http": True, "gs_http": True})
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/connectivity")

    assert response.status_code == 200
    assert response.json()["checks"]["pm_http"]["ok"] is True
    assert response.json()["checks"]["gs_http"]["ok"] is True
