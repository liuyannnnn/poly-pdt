import asyncio

import pytest

from app.store import MemoryStore
from app.trader import (
    RiskLimits,
    TraderManager,
    football_score_delay_trade,
    football_winrate_gap_buy,
)


async def seed_market(store: MemoryStore, guid: str = "guid-1") -> None:
    await store.set_json(
        f"pm:match:{guid}",
        {
            "guid": guid,
            "slug": "ars-ful-2026-05-03",
            "home_team": "Arsenal",
            "away_team": "Fulham",
            "score_home": 0,
            "score_away": 0,
            "home_ask1": 0.61,
            "home_bid1": 0.59,
            "home_asset_id": "asset-home",
            "draw_ask1": 0.25,
            "draw_bid1": 0.23,
            "draw_asset_id": "asset-draw",
            "away_ask1": 0.21,
            "away_bid1": 0.19,
            "away_asset_id": "asset-away",
        },
    )
    await store.set_json(
        f"gs:match:{guid}",
        {
            "guid": guid,
            "score_home": 1,
            "score_away": 0,
            "clock": "34:12",
        },
    )


class FakeClobQuoteClient:
    def __init__(self, quotes: dict[str, dict[str, float | None]]):
        self.quotes = quotes

    async def get_quote(self, asset_id: str) -> dict[str, float | None]:
        return self.quotes[asset_id]


class BlockingClobQuoteClient:
    def __init__(self, block_assets: set[str] | None = None):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block_assets = block_assets

    async def get_quote(self, asset_id: str) -> dict[str, float | None]:
        if self.block_assets is None or asset_id in self.block_assets:
            self.started.set()
            await self.release.wait()
        return {"ask1": 0.64, "bid1": 0.63}


class BlockingFirstClobQuoteClient:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def get_quote(self, asset_id: str) -> dict[str, float | None]:
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        return {"ask1": 0.64, "bid1": 0.63}


@pytest.mark.asyncio
async def test_simulation_buy_and_sell_use_ask_and_bid_and_update_account():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {
                "initial_balance": 1000,
                "stake_usd": 100,
                "risk": {"max_positions": 3, "max_fund_usage_pct": 80, "max_single_order_pct": 20},
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )

    buy = await manager.api(trading.trading_id).buy("guid-1", "home", 100)
    sell = await manager.api(trading.trading_id).sell("guid-1", "home", buy["shares"] / 2)

    account = manager.get_account(trading.trading_id)
    positions = manager.get_positions(trading.trading_id)
    trades = manager.get_trades(trading.trading_id)
    account_snapshot = await store.get_json(f"trader:{trading.trading_id}:account")
    positions_snapshot = await store.get_json(f"trader:{trading.trading_id}:positions")
    trades_snapshot = await store.stream(f"stream:trader:{trading.trading_id}:trades")

    assert buy["price"] == 0.61
    assert buy["shares"] == 163
    assert buy["amount_usd"] == pytest.approx(99.43)
    assert sell["price"] == 0.59
    assert sell["shares"] == 81
    assert sell["amount_usd"] == pytest.approx(47.79)
    assert sell["cost_basis"] == pytest.approx(49.41)
    assert sell["profit"] == pytest.approx(-1.62)
    assert sell["profit_rate"] == pytest.approx(-3.2786885, rel=1e-4)
    assert account.available_cash == pytest.approx(948.36, rel=1e-4)
    assert account.equity == pytest.approx(996.74, rel=1e-4)
    assert positions[0].shares == pytest.approx(82, rel=1e-4)
    assert [trade["side"] for trade in trades] == ["buy", "sell"]
    assert len(buy["order_id"]) == 16
    assert buy["slug"] == "ars-ful-2026-05-03"
    assert buy["team_label"] == "Home"
    assert buy["team_name"] == "Arsenal"
    assert positions[0].order_id == buy["order_id"]
    assert positions[0].slug == "ars-ful-2026-05-03"
    assert positions[0].team_label == "Home"
    assert positions[0].team_name == "Arsenal"
    assert account_snapshot["available_cash"] == pytest.approx(948.36, rel=1e-4)
    assert positions_snapshot[0]["outcome_key"] == "home"
    assert positions_snapshot[0]["order_id"] == buy["order_id"]
    assert [trade["side"] for trade in trades_snapshot] == ["buy", "sell"]


@pytest.mark.asyncio
async def test_buy_budget_is_dollar_amount_and_clob_quote_replaces_stale_ws_quote():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(
        store=store,
        clob_quote_client=FakeClobQuoteClient({"asset-home": {"ask1": 0.70, "bid1": 0.68}}),
    )
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {
                "initial_balance": 10000,
                "risk": {"max_positions": 3, "max_fund_usage_pct": 80, "max_single_order_pct": 20},
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )

    trade = await manager.api(trading.trading_id).buy("guid-1", "home", 2000)

    assert trade["price"] == 0.70
    assert trade["shares"] == 2857
    assert trade["amount_usd"] == pytest.approx(1999.90)
    assert manager.get_account(trading.trading_id).available_cash == pytest.approx(8000.10)
    assert any("CLOB报价与WS差异超过0.01" in log["message"] for log in manager.get_logs(trading.trading_id))


@pytest.mark.asyncio
async def test_quote_uses_conservative_clob_and_ws_price_without_warning_on_equal_one_cent_gap():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update({"home_ask1": 0.75, "home_bid1": 0.74})
    await store.set_json("pm:match:guid-1", pm)
    manager = TraderManager(
        store=store,
        clob_quote_client=FakeClobQuoteClient({"asset-home": {"ask1": 0.76, "bid1": 0.75}}),
    )
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    api = manager.api(trading.trading_id)

    buy = await api.buy("guid-1", "home", 100)
    sell = await api.sell("guid-1", "home", buy["shares"])

    assert buy["price"] == pytest.approx(0.76)
    assert sell["price"] == pytest.approx(0.74)
    assert not any("CLOB报价与WS差异超过0.01" in log["message"] for log in manager.get_logs(trading.trading_id))


@pytest.mark.asyncio
async def test_trader_ids_use_mode_letter_and_three_digit_sequence():
    manager = TraderManager(store=MemoryStore())

    simulation = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    real = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"real_dry_run": True},
            "affect_sports": ["football"],
            "mode": "real",
        }
    )

    assert simulation.trading_id == "S001"
    assert real.trading_id == "R001"


@pytest.mark.asyncio
async def test_delete_trading_removes_persisted_redis_snapshots():
    store = MemoryStore()
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )

    assert await store.get_json(f"trader:{trading.trading_id}:account") is not None

    deleted = await manager.delete_trading(trading.trading_id)

    assert deleted is True
    for suffix in ("config", "state", "account", "positions", "trades", "logs"):
        assert await store.get_text(f"trader:{trading.trading_id}:{suffix}") is None


@pytest.mark.asyncio
async def test_unknown_trading_mutations_do_not_create_placeholder():
    store = MemoryStore()
    manager = TraderManager(store=store)

    with pytest.raises(KeyError):
        await manager.start_trading("S999")
    with pytest.raises(KeyError):
        await manager.stop_trading("S999")
    with pytest.raises(KeyError):
        await manager.update_trading("S999", {"strategy_params": {"x": 1}})

    assert await store.keys("trader:*:config") == []
    assert await store.keys("trader:*:account") == []


@pytest.mark.asyncio
async def test_trader_start_hydrates_persisted_real_accounts_from_store():
    store = MemoryStore()
    original = TraderManager(store=store)
    created = await original.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 250, "real_dry_run": True},
            "affect_sports": ["football"],
            "mode": "real",
            "account_alias": "pm-main",
        }
    )

    restarted = TraderManager(store=store)
    await restarted.start()
    started = await restarted.start_trading(created.trading_id)

    account = restarted.get_account(created.trading_id)
    assert started.mode == "real"
    assert account.mode == "real"
    assert account.account_alias == "pm-main"
    assert account.initial_balance == 250


@pytest.mark.asyncio
async def test_trader_hydration_ignores_legacy_trade_and_log_json_lists():
    store = MemoryStore()
    original = TraderManager(store=store)
    created = await original.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await store.set_json(
        f"trader:{created.trading_id}:trades",
        [{"guid": "legacy-guid", "side": "buy", "ts_utc": "2026-05-01T00:00:00Z"}],
    )
    await store.set_json(
        f"trader:{created.trading_id}:logs",
        [{"guid": "legacy-guid", "message": "legacy", "ts_utc": "2026-05-01T00:00:00Z"}],
    )

    restarted = TraderManager(store=store)
    await restarted.start()

    try:
        assert restarted.get_trades(created.trading_id) == []
        assert restarted.get_logs(created.trading_id) == []
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_trader_validator_rejects_missing_quote_and_risk_breaches():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {
                "initial_balance": 100,
                "risk": {"max_positions": 0, "max_fund_usage_pct": 50, "max_single_order_pct": 10},
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )

    with pytest.raises(ValueError, match="max_positions"):
        await manager.api(trading.trading_id).buy("guid-1", "home", 5)

    manager.set_risk_limits(
        trading.trading_id,
        RiskLimits(max_positions=3, max_fund_usage_pct=50, max_single_order_pct=10),
    )
    with pytest.raises(ValueError, match="max_single_order_pct"):
        await manager.api(trading.trading_id).buy("guid-1", "home", 20)

    await store.set_json("pm:match:no-quote", {"guid": "no-quote"})
    with pytest.raises(ValueError, match="ask1"):
        await manager.api(trading.trading_id).buy("no-quote", "home", 5)


@pytest.mark.asyncio
async def test_trader_rejects_single_add_over_add_fund_limit():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {
                "initial_balance": 1000,
                "risk": {
                    "max_positions": 3,
                    "max_fund_usage_pct": 90,
                    "max_single_order_pct": 50,
                    "max_add_count": 2,
                    "max_add_fund_pct": 10,
                },
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )

    await manager.api(trading.trading_id).buy("guid-1", "home", 100)

    with pytest.raises(ValueError, match="max_add_fund_pct"):
        await manager.api(trading.trading_id).buy("guid-1", "home", 200)


@pytest.mark.asyncio
async def test_real_mode_is_dry_run_and_never_mutates_cash_or_positions():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000, "real_dry_run": True},
            "affect_sports": ["football"],
            "mode": "real",
            "account_alias": "pm-main",
        }
    )

    result = await manager.api(trading.trading_id).buy("guid-1", "home", 100)

    assert result["dry_run"] is True
    assert result["submitted"] is False
    assert manager.get_account(trading.trading_id).available_cash == 1000
    assert manager.get_positions(trading.trading_id) == []


@pytest.mark.asyncio
async def test_real_trader_without_balance_snapshot_defaults_to_zero_cash():
    manager = TraderManager(store=MemoryStore())
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"real_dry_run": True},
            "affect_sports": ["football"],
            "mode": "real",
            "account_alias": "pm-main",
        }
    )

    account = manager.get_account(trading.trading_id)
    assert account.initial_balance == 0
    assert account.available_cash == 0
    assert account.equity == 0


@pytest.mark.asyncio
async def test_default_strategy_detects_score_delay_and_respects_time_and_price_guards():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"stake_usd": 100, "initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    api = manager.api(trading.trading_id)

    intents = await football_score_delay_trade(api, "guid-1", _asa_score_event("guid-1", 0, 0, 1, 0))
    assert intents == [
        {
            "action": "buy",
            "guid": "guid-1",
            "outcome_key": "home",
                "amount_usd": 200.0,
                "reason": "进球买入",
            }
        ]

    await store.set_json(
        "gs:match:guid-1",
        {"guid": "guid-1", "score_home": 1, "score_away": 0, "clock": "85:01"},
    )
    assert await football_score_delay_trade(api, "guid-1", _asa_score_event("guid-1", 0, 0, 1, 0)) == [
        {
            "action": "buy",
            "guid": "guid-1",
            "outcome_key": "home",
            "amount_usd": 100.0,
            "reason": "进球买入",
        }
    ]

    await store.set_json(
        "gs:match:guid-1",
        {"guid": "guid-1", "score_home": 1, "score_away": 0, "clock": "34:12"},
    )
    pm = await store.get_json("pm:match:guid-1")
    pm["home_ask1"] = 0.94
    await store.set_json("pm:match:guid-1", pm)
    assert await football_score_delay_trade(api, "guid-1", _asa_score_event("guid-1", 0, 0, 1, 0)) == [
        {
            "action": "log",
            "guid": "guid-1",
            "reason": "进球但高于0.93不买入",
        }
    ]


@pytest.mark.asyncio
async def test_default_strategy_reverses_opposite_position():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"stake_usd": 100, "initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.api(trading.trading_id).buy("guid-1", "away", 100)

    intents = await football_score_delay_trade(
        manager.api(trading.trading_id),
        "guid-1",
        _asa_score_event("guid-1", 0, 0, 1, 0),
    )

    assert intents[0]["action"] == "sell"
    assert intents[0]["outcome_key"] == "away"
    assert intents[0]["reason"] == "反向进球卖出"
    assert intents[1] == {
        "action": "buy",
        "guid": "guid-1",
        "outcome_key": "home",
            "amount_usd": pytest.approx(198.096),
            "reason": "进球买入",
        }


@pytest.mark.asyncio
async def test_score_delay_strategy_requires_external_score_change_before_pm_score():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"stake_usd": 100, "initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    api = manager.api(trading.trading_id)

    assert await football_score_delay_trade(api, "guid-1", {"source": "pm_sports", "changed_fields": ["score_home"]}) == []
    assert await football_score_delay_trade(api, "guid-1", {"source": "asa_live", "changed_fields": ["clock"]}) == []

    pm = await store.get_json("pm:match:guid-1")
    pm["score_home"] = 1
    await store.set_json("pm:match:guid-1", pm)
    assert await football_score_delay_trade(api, "guid-1", _asa_score_event("guid-1", 0, 0, 1, 0)) == []


@pytest.mark.asyncio
async def test_score_delay_strategy_buys_draw_when_external_equalizes_before_pm():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update({"score_home": 1, "score_away": 0, "draw_ask1": 0.44, "draw_bid1": 0.43})
    await store.set_json("pm:match:guid-1", pm)
    await store.set_json("gs:match:guid-1", {"guid": "guid-1", "score_home": 1, "score_away": 1, "clock": "53:00"})
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"stake_usd": 100, "initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )

    assert await football_score_delay_trade(
        manager.api(trading.trading_id),
        "guid-1",
        _asa_score_event("guid-1", 1, 0, 1, 1),
    ) == [
        {
            "action": "buy",
            "guid": "guid-1",
            "outcome_key": "draw",
            "amount_usd": 200.0,
            "reason": "追平买入",
        }
    ]


@pytest.mark.asyncio
async def test_score_delay_strategy_uses_pm_score_snapshot_from_external_event_when_queue_lags():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update({"score_home": 1, "score_away": 1, "draw_ask1": 0.44, "draw_bid1": 0.43})
    await store.set_json("pm:match:guid-1", pm)
    await store.set_json("gs:match:guid-1", {"guid": "guid-1", "score_home": 1, "score_away": 1, "clock": "53:00"})
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )

    event = {
        **_asa_score_event("guid-1", 1, 0, 1, 1),
        "pm_score_home_at_event": 1,
        "pm_score_away_at_event": 0,
    }

    assert await football_score_delay_trade(manager.api(trading.trading_id), "guid-1", event) == [
        {
            "action": "buy",
            "guid": "guid-1",
            "outcome_key": "draw",
            "amount_usd": 200.0,
            "reason": "追平买入",
        }
    ]


@pytest.mark.asyncio
async def test_score_delay_strategy_logs_when_trailing_team_scores_but_no_rule_triggers():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update({"score_home": 0, "score_away": 3, "away_ask1": 0.95, "away_bid1": 0.94})
    await store.set_json("pm:match:guid-1", pm)
    await store.set_json("gs:match:guid-1", {"guid": "guid-1", "score_home": 1, "score_away": 3, "clock": "75:00"})
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )

    assert await football_score_delay_trade(
        manager.api(trading.trading_id),
        "guid-1",
        _asa_score_event("guid-1", 0, 3, 1, 3),
    ) == [
        {
            "action": "log",
            "guid": "guid-1",
            "reason": "落后方进球但领先优势仍大于1不操作",
        }
    ]


@pytest.mark.asyncio
async def test_score_delay_strategy_does_not_repeat_buy_on_clock_or_pm_events():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"stake_usd": 100, "initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)

    manager.enqueue_event(_asa_score_event("guid-1", 0, 0, 1, 0))
    assert await manager.process_queued_events() == {"processed": 1, "trades": 1, "failures": 0}
    manager.enqueue_event({"guid": "guid-1", "source": "asa_live", "changed_fields": ["clock"]})
    manager.enqueue_event({"guid": "guid-1", "source": "pm_sports", "changed_fields": ["match_time"]})
    assert await manager.process_queued_events() == {"processed": 2, "trades": 0, "failures": 0}
    assert len(manager.get_trades(trading.trading_id)) == 1


@pytest.mark.asyncio
async def test_market_ticks_use_separate_channel_and_do_not_enter_strategy_queue():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)

    manager.on_match_signal(_asa_score_event("guid-1", 0, 0, 1, 0))
    manager.on_match_signal(_asa_score_event("guid-1", 1, 0, 2, 0))
    await manager.on_market_tick(
        {
            "source": "pm_market",
            "event_type": "market_tick",
            "guid": "guid-1",
            "outcome_key": "home",
            "ask1": 0.61,
            "bid1": 0.59,
        }
    )

    assert manager.queue_size(trading.trading_id) == 2


@pytest.mark.asyncio
async def test_market_tick_channel_checks_drawdown_without_running_strategy_queue():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {
                "initial_balance": 1000,
                "risk": {"max_positions": 3, "max_fund_usage_pct": 90, "max_single_order_pct": 20},
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)
    api = manager.api(trading.trading_id)
    await api.buy("guid-1", "home", 100)
    pm = await store.get_json("pm:match:guid-1")

    await store.set_json("pm:match:guid-1", {**pm, "home_ask1": 0.70, "home_bid1": 0.69})
    await manager.on_market_tick(
        {
            "source": "pm_market",
            "event_type": "market_tick",
            "guid": "guid-1",
            "outcome_key": "home",
            "ask1": 0.70,
            "bid1": 0.69,
        }
    )
    assert manager.queue_size(trading.trading_id) == 0
    assert len(manager.get_trades(trading.trading_id)) == 1

    await store.set_json("pm:match:guid-1", {**pm, "home_ask1": 0.64, "home_bid1": 0.63})
    await manager.on_market_tick(
        {
            "source": "pm_market",
            "event_type": "market_tick",
            "guid": "guid-1",
            "outcome_key": "home",
            "ask1": 0.64,
            "bid1": 0.63,
        }
    )

    assert manager.queue_size(trading.trading_id) == 0
    await _wait_until(lambda: [trade["side"] for trade in manager.get_trades(trading.trading_id)] == ["buy", "sell"])
    assert manager.get_trades(trading.trading_id)[-1]["reason"] == "回撤0.05卖出"


@pytest.mark.asyncio
async def test_market_tick_channel_uses_in_memory_quote_before_redis_snapshot():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {
                "initial_balance": 1000,
                "risk": {"max_positions": 3, "max_fund_usage_pct": 90, "max_single_order_pct": 20},
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)
    await manager.api(trading.trading_id).buy("guid-1", "home", 100)

    await manager.on_market_tick(
        {
            "source": "pm_market",
            "event_type": "market_tick",
            "guid": "guid-1",
            "outcome_key": "home",
            "ask1": 0.70,
            "bid1": 0.69,
        }
    )
    await manager.on_market_tick(
        {
            "source": "pm_market",
            "event_type": "market_tick",
            "guid": "guid-1",
            "outcome_key": "home",
            "ask1": 0.64,
            "bid1": 0.63,
        }
    )

    pm = await store.get_json("pm:match:guid-1")
    assert pm["home_ask1"] == 0.61
    await _wait_until(lambda: [trade["side"] for trade in manager.get_trades(trading.trading_id)] == ["buy", "sell"])


@pytest.mark.asyncio
async def test_market_tick_updates_memory_without_waiting_for_slow_trade_checks():
    store = MemoryStore()
    await seed_market(store)
    blocking_clob = BlockingClobQuoteClient()
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {
                "initial_balance": 1000,
                "risk": {"max_positions": 3, "max_fund_usage_pct": 90, "max_single_order_pct": 20},
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)
    await manager.api(trading.trading_id).buy("guid-1", "home", 100)
    manager._clob_quote_client = blocking_clob

    await asyncio.wait_for(
        manager.on_market_tick(
            {
                "source": "pm_market",
                "event_type": "market_tick",
                "guid": "guid-1",
                "outcome_key": "home",
                "ask1": 0.70,
                "bid1": 0.69,
            }
        ),
        timeout=0.05,
    )

    market = await manager.api(trading.trading_id).get_market("guid-1")
    assert market["home"]["ask1"] == 0.70
    assert not blocking_clob.started.is_set()


@pytest.mark.asyncio
async def test_account_events_update_matching_real_trader_only():
    store = MemoryStore()
    manager = TraderManager(store=store)
    pm_trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"real_dry_run": True},
            "affect_sports": ["football"],
            "mode": "real",
            "account_alias": "pm-main",
        }
    )
    other_trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"real_dry_run": True},
            "affect_sports": ["football"],
            "mode": "real",
            "account_alias": "pm-other",
        }
    )

    await manager.on_account_event(
        {
            "source": "pm_user",
            "provider": "pm",
            "account_alias": "pm-main",
            "changed_fields": ["account"],
            "account": {"balance": 123.45, "available_cash": 111.22},
        }
    )

    pm_account = manager.get_account(pm_trading.trading_id)
    other_account = manager.get_account(other_trading.trading_id)
    assert pm_account.initial_balance == pytest.approx(123.45)
    assert pm_account.available_cash == pytest.approx(111.22)
    assert pm_account.equity == pytest.approx(123.45)
    assert other_account.available_cash == 0


@pytest.mark.asyncio
async def test_score_delay_strategy_adds_only_when_leader_scores_again_under_price_cap():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"stake_usd": 100, "initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    api = manager.api(trading.trading_id)
    await api.buy("guid-1", "home", 100)

    assert await football_score_delay_trade(api, "guid-1", _asa_score_event("guid-1", 1, 0, 1, 0)) == [
        {
            "action": "log",
            "guid": "guid-1",
            "reason": "未继续进球不加仓",
        }
    ]

    await store.set_json("gs:match:guid-1", {"guid": "guid-1", "score_home": 2, "score_away": 0, "clock": "35:00"})
    assert await football_score_delay_trade(api, "guid-1", _asa_score_event("guid-1", 1, 0, 2, 0)) == [
        {
            "action": "buy",
            "guid": "guid-1",
            "outcome_key": "home",
            "amount_usd": pytest.approx(99.674),
            "reason": "进球加仓",
        }
    ]

    pm = await store.get_json("pm:match:guid-1")
    pm["home_ask1"] = 0.94
    await store.set_json("pm:match:guid-1", pm)
    await store.set_json("gs:match:guid-1", {"guid": "guid-1", "score_home": 3, "score_away": 0, "clock": "36:00"})
    assert await football_score_delay_trade(api, "guid-1", _asa_score_event("guid-1", 2, 0, 3, 0)) == [
        {
            "action": "log",
            "guid": "guid-1",
            "reason": "进球但高于0.93不加仓",
        }
    ]


@pytest.mark.asyncio
async def test_score_delay_strategy_uses_common_risk_budgets_and_halves_after_85_minutes():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {
                "initial_balance": 10000,
                "risk": {
                    "max_positions": 3,
                    "max_fund_usage_pct": 80,
                    "max_single_order_pct": 20,
                    "max_add_count": 2,
                    "max_add_fund_pct": 10,
                },
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    api = manager.api(trading.trading_id)

    assert await football_score_delay_trade(api, "guid-1", _asa_score_event("guid-1", 0, 0, 1, 0)) == [
        {
            "action": "buy",
            "guid": "guid-1",
            "outcome_key": "home",
            "amount_usd": 2000.0,
            "reason": "进球买入",
        }
    ]

    await api.buy("guid-1", "home", 2000)
    await store.set_json("gs:match:guid-1", {"guid": "guid-1", "score_home": 2, "score_away": 0, "clock": "86:00"})
    assert await football_score_delay_trade(api, "guid-1", _asa_score_event("guid-1", 1, 0, 2, 0)) == [
        {
            "action": "buy",
            "guid": "guid-1",
            "outcome_key": "home",
            "amount_usd": pytest.approx(api._instance.account.equity * 0.05),
            "reason": "进球加仓",
        }
    ]


@pytest.mark.asyncio
async def test_score_delay_strategy_sells_when_two_goal_lead_shrinks_to_one_with_profit():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update({"home_ask1": 0.60, "home_bid1": 0.59})
    await store.set_json("pm:match:guid-1", pm)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 10000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    api = manager.api(trading.trading_id)
    buy = await api.buy("guid-1", "home", 1000)
    pm["home_bid1"] = 0.86
    await store.set_json("pm:match:guid-1", pm)
    await store.set_json("gs:match:guid-1", {"guid": "guid-1", "score_home": 3, "score_away": 2, "clock": "72:00"})

    assert await football_score_delay_trade(api, "guid-1", _asa_score_event("guid-1", 3, 1, 3, 2)) == [
        {
            "action": "sell",
            "guid": "guid-1",
            "outcome_key": "home",
            "shares": buy["shares"],
            "reason": "领先优势缩小平仓",
        }
    ]


@pytest.mark.asyncio
async def test_score_delay_protective_exit_runs_when_pm_already_has_previous_score():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update({"score_home": 3, "score_away": 1, "home_ask1": 0.60, "home_bid1": 0.90})
    await store.set_json("pm:match:guid-1", pm)
    await store.set_json("gs:match:guid-1", {"guid": "guid-1", "score_home": 3, "score_away": 2, "clock": "72:00"})
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 10000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    api = manager.api(trading.trading_id)
    buy = await api.buy("guid-1", "home", 1000)

    assert await football_score_delay_trade(api, "guid-1", _asa_score_event("guid-1", 3, 1, 3, 2)) == [
        {
            "action": "sell",
            "guid": "guid-1",
            "outcome_key": "home",
            "shares": buy["shares"],
            "reason": "领先优势缩小平仓",
        }
    ]


@pytest.mark.asyncio
async def test_winrate_gap_strategy_buys_higher_team_inside_prematch_window():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update(
        {
            "start_time_utc": "2026-05-02T12:00:00Z",
            "status": "pre",
            "home_ask1": 0.64,
            "away_ask1": 0.30,
        }
    )
    await store.set_json("pm:match:guid-1", pm)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_winrate_gap_buy",
            "strategy_params": {
                "stake_usd": 50,
                "initial_balance": 1000,
                "now_utc": "2026-05-02T11:56:00Z",
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )

    intents = await football_winrate_gap_buy(manager.api(trading.trading_id), "guid-1")

    assert intents == [
        {
            "action": "buy",
            "guid": "guid-1",
            "outcome_key": "home",
            "amount_usd": 200.0,
            "reason": "胜率差买入",
        }
    ]


@pytest.mark.asyncio
async def test_winrate_gap_strategy_buys_after_kickoff_only_when_scoreless():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update(
        {
            "start_time_utc": "2026-05-02T12:00:00Z",
            "status": "live",
            "score_home": 0,
            "score_away": 0,
            "home_ask1": 0.28,
            "away_ask1": 0.61,
        }
    )
    await store.set_json("pm:match:guid-1", pm)
    await store.set_json("gs:match:guid-1", {"guid": "guid-1", "score_home": 0, "score_away": 0, "clock": "12:21"})
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_winrate_gap_buy",
            "strategy_params": {
                "stake_usd": 75,
                "initial_balance": 1000,
                "now_utc": "2026-05-02T12:12:00Z",
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    api = manager.api(trading.trading_id)

    assert await football_winrate_gap_buy(api, "guid-1") == [
        {
            "action": "buy",
            "guid": "guid-1",
            "outcome_key": "away",
            "amount_usd": 200.0,
            "reason": "胜率差买入",
        }
    ]

    await store.set_json("gs:match:guid-1", {"guid": "guid-1", "score_home": 1, "score_away": 0, "clock": "13:00"})
    assert await football_winrate_gap_buy(api, "guid-1") == []


@pytest.mark.asyncio
async def test_winrate_gap_strategy_sells_on_drawdown_or_finished_match():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update(
        {
            "start_time_utc": "2026-05-02T12:00:00Z",
            "status": "live",
            "home_ask1": 0.64,
            "home_bid1": 0.63,
            "away_ask1": 0.30,
        }
    )
    await store.set_json("pm:match:guid-1", pm)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_winrate_gap_buy",
            "strategy_params": {
                "stake_usd": 100,
                "initial_balance": 1000,
                "now_utc": "2026-05-02T12:05:00Z",
                "risk": {"stop_loss_drawdown": 0.05},
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    api = manager.api(trading.trading_id)
    buy = await api.buy("guid-1", "home", 100)
    pm["home_ask1"] = 0.58
    pm["home_bid1"] = 0.57
    await store.set_json("pm:match:guid-1", pm)

    drawdown_intents = await football_winrate_gap_buy(api, "guid-1")

    assert drawdown_intents == [
        {
            "action": "sell",
            "guid": "guid-1",
            "outcome_key": "home",
            "shares": buy["shares"],
            "reason": "回撤0.05卖出",
        }
    ]

    pm["home_ask1"] = 0.64
    pm["home_bid1"] = 0.63
    pm["status"] = "finished"
    await store.set_json("pm:match:guid-1", pm)
    assert await football_winrate_gap_buy(api, "guid-1") == [
        {
            "action": "sell",
            "guid": "guid-1",
            "outcome_key": "home",
            "shares": buy["shares"],
            "reason": "比赛结束卖出",
        }
    ]


@pytest.mark.asyncio
async def test_common_drawdown_uses_absolute_price_drop_from_peak_not_percent():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update({"home_ask1": 0.20, "home_bid1": 0.19, "status": "live"})
    await store.set_json("pm:match:guid-1", pm)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {
                "initial_balance": 1000,
                "risk": {"stop_loss_drawdown": 0.05},
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    api = manager.api(trading.trading_id)
    await api.buy("guid-1", "home", 100)

    assert await football_score_delay_trade(api, "guid-1", {"source": "asa_live", "changed_fields": ["clock"]}) == []

    pm["home_ask1"] = 0.55
    pm["home_bid1"] = 0.54
    await store.set_json("pm:match:guid-1", pm)
    assert await football_winrate_gap_buy(api, "guid-1") == []
    assert manager.get_positions(trading.trading_id)[0].peak_price == pytest.approx(0.55)

    pm["home_ask1"] = 0.50
    pm["home_bid1"] = 0.49
    await store.set_json("pm:match:guid-1", pm)

    assert await football_winrate_gap_buy(api, "guid-1") == [
        {
            "action": "sell",
            "guid": "guid-1",
            "outcome_key": "home",
            "shares": 500,
            "reason": "回撤0.05卖出",
        }
    ]


@pytest.mark.asyncio
async def test_common_drawdown_uses_ask_peak_not_bid_drop():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update({"home_ask1": 0.23, "home_bid1": 0.22, "status": "live"})
    await store.set_json("pm:match:guid-1", pm)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {
                "initial_balance": 1000,
                "risk": {"stop_loss_drawdown": 0.05},
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    api = manager.api(trading.trading_id)
    await api.buy("guid-1", "home", 100)

    pm["home_bid1"] = 0.17
    await store.set_json("pm:match:guid-1", pm)
    assert await football_winrate_gap_buy(api, "guid-1") == []

    pm["home_ask1"] = 0.65
    pm["home_bid1"] = 0.64
    await store.set_json("pm:match:guid-1", pm)
    assert await football_winrate_gap_buy(api, "guid-1") == []

    pm["home_ask1"] = 0.60
    pm["home_bid1"] = 0.59
    await store.set_json("pm:match:guid-1", pm)
    assert await football_winrate_gap_buy(api, "guid-1") == [
        {
            "action": "sell",
            "guid": "guid-1",
            "outcome_key": "home",
            "shares": 434,
            "reason": "回撤0.05卖出",
        }
    ]


@pytest.mark.asyncio
async def test_common_drawdown_exit_runs_before_score_delay_strategy():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update({"home_ask1": 0.77, "home_bid1": 0.70, "status": "live"})
    await store.set_json("pm:match:guid-1", pm)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {
                "initial_balance": 1000,
                "risk": {"stop_loss_drawdown": 0.05},
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)
    buy = await manager.api(trading.trading_id).buy("guid-1", "home", 100)
    pm["home_ask1"] = 0.71
    pm["home_bid1"] = 0.70
    await store.set_json("pm:match:guid-1", pm)

    manager.enqueue_event({"guid": "guid-1"})
    result = await manager.process_queued_events()

    assert result == {"processed": 1, "trades": 1, "failures": 0}
    assert manager.get_positions(trading.trading_id) == []
    assert manager.get_trades(trading.trading_id)[-1]["side"] == "sell"
    assert manager.get_trades(trading.trading_id)[-1]["shares"] == buy["shares"]
    assert manager.get_trades(trading.trading_id)[-1]["reason"] == "回撤0.05卖出"


@pytest.mark.asyncio
async def test_winrate_gap_strategy_does_not_reenter_after_exit():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update(
        {
            "start_time_utc": "2026-05-02T12:00:00Z",
            "status": "live",
            "score_home": 0,
            "score_away": 0,
            "home_ask1": 0.64,
            "home_bid1": 0.63,
            "away_ask1": 0.30,
        }
    )
    await store.set_json("pm:match:guid-1", pm)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_winrate_gap_buy",
            "strategy_params": {
                "stake_usd": 100,
                "initial_balance": 1000,
                "now_utc": "2026-05-02T12:05:00Z",
                "risk": {"stop_loss_drawdown": 0.05},
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)
    api = manager.api(trading.trading_id)
    buy = await api.buy("guid-1", "home", 100)
    await api.sell("guid-1", "home", buy["shares"])

    intents = await football_winrate_gap_buy(api, "guid-1")

    assert intents == []


@pytest.mark.asyncio
async def test_trader_queue_preserves_duplicate_match_events():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)

    manager.enqueue_event({"guid": "guid-1"})
    manager.enqueue_event({"guid": "guid-1"})
    manager.enqueue_event({"guid": "guid-2"})

    assert manager.queue_size(trading.trading_id) == 3


@pytest.mark.asyncio
async def test_trader_event_dispatches_winrate_gap_strategy():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update(
        {
            "start_time_utc": "2026-05-02T12:00:00Z",
            "status": "pre",
            "home_ask1": 0.64,
            "away_ask1": 0.30,
        }
    )
    await store.set_json("pm:match:guid-1", pm)
    manager = TraderManager(store=store)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_winrate_gap_buy",
            "strategy_params": {
                "stake_usd": 50,
                "initial_balance": 1000,
                "now_utc": "2026-05-02T11:56:00Z",
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)
    manager.enqueue_event({"guid": "guid-1"})

    result = await manager.process_queued_events()

    assert result == {"processed": 1, "trades": 1, "failures": 0}
    assert manager.get_trades(trading.trading_id)[0]["side"] == "buy"
    assert manager.get_positions(trading.trading_id)[0].outcome_key == "home"


@pytest.mark.asyncio
async def test_trader_start_processes_queued_events_in_background():
    store = MemoryStore()
    await seed_market(store)
    pm = await store.get_json("pm:match:guid-1")
    pm.update(
        {
            "start_time_utc": "2026-05-02T12:00:00Z",
            "status": "pre",
            "home_ask1": 0.64,
            "away_ask1": 0.30,
        }
    )
    await store.set_json("pm:match:guid-1", pm)
    manager = TraderManager(store=store, process_interval_seconds=0.01)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_winrate_gap_buy",
            "strategy_params": {
                "stake_usd": 50,
                "initial_balance": 1000,
                "now_utc": "2026-05-02T11:56:00Z",
            },
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)
    await manager.start()
    try:
        manager.enqueue_event({"guid": "guid-1"})
        await _wait_until(lambda: len(manager.get_trades(trading.trading_id)) == 1)
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_trader_background_processing_is_isolated_per_trader():
    store = MemoryStore()
    await seed_market(store)
    blocking_clob = BlockingFirstClobQuoteClient()
    slow_manager = TraderManager(
        store=store,
        clob_quote_client=blocking_clob,
        process_interval_seconds=0.01,
    )
    slow = await slow_manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    fast = await slow_manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await slow_manager.start()
    await slow_manager.start_trading(slow.trading_id)
    await slow_manager.start_trading(fast.trading_id)

    try:
        slow_manager.on_match_signal(_asa_score_event("guid-1", 0, 0, 1, 0))
        await asyncio.wait_for(blocking_clob.started.wait(), timeout=0.2)

        async def fast_traded() -> bool:
            for _ in range(30):
                if slow_manager.get_trades(fast.trading_id):
                    return True
                await asyncio.sleep(0.01)
            return False

        assert await fast_traded() is True
    finally:
        await slow_manager.stop()


@pytest.mark.asyncio
async def test_trader_background_loop_wakes_immediately_when_event_is_enqueued():
    store = MemoryStore()
    await seed_market(store)
    manager = TraderManager(store=store, process_interval_seconds=3600)
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)
    await manager.start()
    try:
        await asyncio.sleep(0.02)
        manager.enqueue_event(_asa_score_event("guid-1", 0, 0, 1, 0))
        await _wait_until(lambda: len(manager.get_trades(trading.trading_id)) == 1, timeout_seconds=0.3)
    finally:
        await manager.stop()

    assert manager.get_trades(trading.trading_id)[0]["reason"] == "进球买入"


@pytest.mark.asyncio
async def test_trader_preserves_multiple_score_events_for_same_match():
    manager = TraderManager(store=MemoryStore())
    trading = await manager.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await manager.start_trading(trading.trading_id)

    manager.enqueue_event(_asa_score_event("guid-1", 0, 0, 1, 0))
    manager.enqueue_event(_asa_score_event("guid-1", 1, 0, 2, 0))

    assert manager.queue_size(trading.trading_id) == 2


@pytest.mark.asyncio
async def test_trader_rejects_unknown_strategy_and_non_football_scope():
    manager = TraderManager(store=MemoryStore())

    with pytest.raises(ValueError, match="unsupported strategy"):
        await manager.create_trading(
            {
                "strategy_name": "unknown",
                "strategy_params": {},
                "affect_sports": ["football"],
                "mode": "simulation",
            }
        )

    with pytest.raises(ValueError, match="football"):
        await manager.create_trading(
            {
                "strategy_name": "football_score_delay_trade",
                "strategy_params": {},
                "affect_sports": ["basketball"],
                "mode": "simulation",
            }
        )


async def _wait_until(predicate, timeout_seconds: float = 0.5) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def _asa_score_event(
    guid: str,
    previous_home: int,
    previous_away: int,
    score_home: int,
    score_away: int,
) -> dict[str, object]:
    return {
        "guid": guid,
        "source": "asa_live",
        "changed_fields": ["score_home", "score_away"],
        "previous_score_home": previous_home,
        "previous_score_away": previous_away,
        "score_home": score_home,
        "score_away": score_away,
    }
