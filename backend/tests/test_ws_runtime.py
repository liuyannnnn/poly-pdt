import sys

import pytest

from app.config import load_settings
from app.polymarket import PMMarketWsSource
from app.store import MemoryStore


class RecordingWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


def test_runtime_defaults_monitor_fixed_ws_connections(monkeypatch):
    monkeypatch.delenv("PM_SPORTS_WS_ENABLED", raising=False)
    monkeypatch.delenv("PM_MARKET_WS_ENABLED", raising=False)
    monkeypatch.delenv("GOALSERVE_WS_ENABLED", raising=False)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    settings = load_settings()

    assert settings.pm_sports_ws_enabled is True
    assert settings.pm_market_ws_enabled is True
    assert settings.goalserve_ws_enabled is False
    assert settings.allsports_ws_enabled is True


@pytest.mark.asyncio
async def test_pm_market_ws_subscribe_current_assets_only_sends_new_assets():
    store = MemoryStore()
    await store.set_text("idx:pm:asset:asset-1", "guid-1|home")
    websocket = RecordingWebSocket()
    source = PMMarketWsSource(store=store)

    first = await source.subscribe_current_assets(websocket)
    await store.set_text("idx:pm:asset:asset-2", "guid-1|away")
    second = await source.subscribe_current_assets(websocket)
    third = await source.subscribe_current_assets(websocket)

    assert first == 1
    assert second == 1
    assert third == 0
    assert "asset-1" in websocket.messages[0]
    assert "asset-2" in websocket.messages[1]
    assert len(websocket.messages) == 2


def test_pm_market_ws_filters_price_changes_to_subscribed_assets():
    source = PMMarketWsSource(store=MemoryStore())
    source._subscribed_asset_ids.add("asset-yes")

    rows = source.normalize_subscribed_payloads(
        {
            "event_type": "price_change",
            "timestamp": "1777730011885",
            "price_changes": [
                {"asset_id": "asset-yes", "best_bid": "0.99", "best_ask": "1"},
                {"asset_id": "asset-no", "best_bid": "0", "best_ask": "0.001"},
            ],
        }
    )

    assert rows == [{"asset_id": "asset-yes", "bid": 0.99, "ask": 1.0, "ts": "1777730011885"}]
