"""Trader 通用模块：策略只产出意图，买卖、风控、持仓和 dry-run 都在这里执行。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict, dataclass, field, fields as dataclass_fields
from datetime import UTC, datetime
import re
from typing import Any
import uuid

from .models import TradingCreatePayload, TradingSnapshot, TradingUpdatePayload


SCORE_DELAY_STRATEGY = "football_score_delay_trade"
WINRATE_GAP_STRATEGY = "football_winrate_gap_buy"
SUPPORTED_STRATEGY = SCORE_DELAY_STRATEGY
SUPPORTED_STRATEGIES = {SCORE_DELAY_STRATEGY, WINRATE_GAP_STRATEGY}
SUPPORTED_SPORTS = ["football"]


@dataclass
class RiskLimits:
    max_positions: int = 3
    max_fund_usage_pct: float = 80.0
    max_single_order_pct: float = 20.0
    max_add_count: int = 2
    max_add_fund_pct: float = 10.0
    stop_loss_drawdown: float = 0.05


@dataclass
class Account:
    trading_id: str
    mode: str
    account_alias: str | None
    initial_balance: float
    available_cash: float
    reserved_cash: float = 0.0
    equity: float = 0.0
    fund_usage_pct: float = 0.0
    position_count: int = 0
    provider: str = "pm"

    def __post_init__(self) -> None:
        if self.equity == 0:
            self.equity = self.initial_balance


@dataclass
class Position:
    guid: str
    outcome_key: str
    shares: float
    avg_entry_price: float
    cost_basis: float
    order_id: str = ""
    slug: str = ""
    team_label: str = ""
    team_name: str = ""
    current_bid1: float | None = None
    current_ask1: float | None = None
    unrealized_pnl: float = 0.0
    add_count: int = 0
    peak_value: float = 0.0
    peak_price: float = 0.0


@dataclass
class TraderInstance:
    snapshot: TradingSnapshot
    account: Account
    risk: RiskLimits
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    positions: list[Position] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    account_alias: str | None = None
    persisted_trade_count: int = 0
    persisted_log_count: int = 0


class TraderManager:
    def __init__(
        self,
        store: Any | None = None,
        process_interval_seconds: float = 1.0,
        clob_quote_client: Any | None = None,
    ):
        self._store = store
        self._clob_quote_client = clob_quote_client
        self._tradings: dict[str, TraderInstance] = {}
        self.simulation_running = False
        self._process_interval_seconds = process_interval_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        self._market_state: dict[str, dict[str, dict[str, float | None]]] = {}

    async def start(self) -> None:
        await self._hydrate_from_store()
        if self._running:
            return None
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        self._wake_event.set()
        return None

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.simulation_running = False
        for instance in self._tradings.values():
            instance.snapshot = instance.snapshot.model_copy(update={"status": "stopped"})

    def list_tradings(self) -> list[TradingSnapshot]:
        return [instance.snapshot for instance in self._tradings.values()]

    async def create_trading(self, payload: TradingCreatePayload | dict[str, Any]) -> TradingSnapshot:
        if isinstance(payload, dict):
            payload = TradingCreatePayload(**payload)
        _validate_scope(payload.strategy_name, payload.affect_sports)
        trading_id = await self._next_trading_id(payload.mode)
        params = payload.strategy_params or {}
        default_initial_balance = 0.0 if payload.mode == "real" else 10000.0
        initial_balance = float(params.get("initial_balance", default_initial_balance))
        snapshot = TradingSnapshot(
            trading_id=trading_id,
            status="stopped",
            mode=payload.mode,
            strategy_name=payload.strategy_name,
            strategy_params=params,
            affect_sports=payload.affect_sports,
        )
        instance = TraderInstance(
            snapshot=snapshot,
            account=Account(
                trading_id=trading_id,
                mode=payload.mode,
                account_alias=payload.account_alias,
                initial_balance=initial_balance,
                available_cash=initial_balance,
                provider=str(params.get("provider") or params.get("account_provider") or "pm").lower(),
            ),
            risk=_risk_from_params(params),
            account_alias=payload.account_alias,
        )
        self._tradings[trading_id] = instance
        await self._persist_instance(instance)
        return snapshot

    async def _next_trading_id(self, mode: str) -> str:
        prefix = "R" if mode == "real" else "S"
        used_numbers: list[int] = []
        for trading_id, instance in self._tradings.items():
            number = _trading_id_number(trading_id, instance.snapshot.mode)
            if number is not None and instance.snapshot.mode == mode:
                used_numbers.append(number)
        if self._store is not None:
            for key in await self._store.keys("trader:*:config"):
                config = await self._store.get_json(key)
                if not config:
                    continue
                config_mode = str(config.get("mode") or "")
                if config_mode != mode:
                    continue
                number = _trading_id_number(str(config.get("trading_id") or ""), config_mode)
                if number is not None:
                    used_numbers.append(number)
        return f"{prefix}{(max(used_numbers) + 1) if used_numbers else 1:03d}"

    async def update_trading(self, trading_id: str, payload: TradingUpdatePayload | dict[str, Any]) -> TradingSnapshot:
        if isinstance(payload, dict):
            payload = TradingUpdatePayload(**payload)
        instance = self._get_existing_instance(trading_id)
        update: dict[str, Any] = {}
        if payload.strategy_params is not None:
            update["strategy_params"] = payload.strategy_params
            instance.risk = _risk_from_params(payload.strategy_params)
        if payload.affect_sports is not None:
            _validate_scope(instance.snapshot.strategy_name, payload.affect_sports)
            update["affect_sports"] = payload.affect_sports
        instance.snapshot = instance.snapshot.model_copy(update=update)
        await self._persist_instance(instance)
        return instance.snapshot

    async def start_trading(self, trading_id: str) -> TradingSnapshot:
        instance = self._get_existing_instance(trading_id)
        instance.snapshot = instance.snapshot.model_copy(update={"status": "running"})
        await self._persist_instance(instance)
        return instance.snapshot

    async def stop_trading(self, trading_id: str) -> TradingSnapshot:
        instance = self._get_existing_instance(trading_id)
        instance.snapshot = instance.snapshot.model_copy(update={"status": "stopped"})
        await self._persist_instance(instance)
        return instance.snapshot

    async def delete_trading(self, trading_id: str) -> bool:
        # 删除要覆盖内存和 Redis，否则前端刷新后会从持久化快照里又读回来。
        existed = self._tradings.pop(trading_id, None) is not None
        if self._store is not None:
            keys = [
                f"trader:{trading_id}:config",
                f"trader:{trading_id}:state",
                f"trader:{trading_id}:account",
                f"trader:{trading_id}:positions",
                f"trader:{trading_id}:trades",
                f"trader:{trading_id}:logs",
                f"stream:trader:{trading_id}:trades",
                f"stream:trader:{trading_id}:logs",
            ]
            for key in keys:
                if await self._store.get_text(key) is not None:
                    existed = True
                await self._store.delete(key)
            if not await self._store.keys("trader:*:account"):
                await self._store.delete("idx:traders")
        return existed

    def start_simulation(self) -> bool:
        self.simulation_running = True
        return self.simulation_running

    def stop_simulation(self) -> bool:
        self.simulation_running = False
        return self.simulation_running

    def api(self, trading_id: str) -> "TraderAPI":
        return TraderAPI(self, trading_id)

    def get_account(self, trading_id: str) -> Account:
        return self._get_existing_instance(trading_id).account

    def get_positions(self, trading_id: str) -> list[Position]:
        return list(self._get_existing_instance(trading_id).positions)

    def get_trades(self, trading_id: str) -> list[dict[str, Any]]:
        return list(self._get_existing_instance(trading_id).trades)

    def get_logs(self, trading_id: str) -> list[dict[str, Any]]:
        return list(self._get_existing_instance(trading_id).logs)

    def set_risk_limits(self, trading_id: str, risk: RiskLimits) -> None:
        self._get_existing_instance(trading_id).risk = risk

    def on_match_signal(self, event: dict[str, Any]) -> None:
        """比赛信号入口：只接收判别器产出的比赛变化事件。

        这里不再按 guid 合并。比分、红黄牌、状态等比赛信号都有时序意义，
        高频盘口 tick 走 on_market_tick，不会进入这个策略队列。
        """

        queued = False
        for instance in self._tradings.values():
            if instance.snapshot.status == "running":
                instance.queue.put_nowait(event)
                queued = True
        if queued:
            self._wake_event.set()

    def enqueue_event(self, event: dict[str, Any]) -> None:
        # 兼容 Collector 和旧测试入口；语义等同于比赛信号入队，不做 guid 去重。
        self.on_match_signal(event)

    async def on_market_tick(self, event: dict[str, Any]) -> None:
        """盘口 tick 入口：只用于持仓估值和通用强制平仓，不运行策略。"""

        guid = str(event.get("guid") or "")
        if not guid:
            return
        self._update_market_state(event)
        for instance in list(self._tradings.values()):
            if instance.snapshot.status != "running":
                continue
            if not any(position.guid == guid and position.shares > 0 for position in instance.positions):
                continue
            await self._process_market_tick(instance, event)

    async def on_account_event(self, event: dict[str, Any]) -> None:
        """账户事件入口：PM/未来 KS user stream 直接更新对应实盘交易员账户。"""

        alias = event.get("account_alias")
        provider = str(event.get("provider") or "pm").lower()
        account_row = event.get("account") if isinstance(event.get("account"), dict) else {}
        for instance in list(self._tradings.values()):
            if instance.snapshot.mode != "real":
                continue
            if instance.account.account_alias != alias:
                continue
            if str(instance.account.provider or "pm").lower() != provider:
                continue
            balance = _first_optional_number(account_row, "balance", "equity", "total_assets", "total_balance")
            available = _first_optional_number(account_row, "available_cash", "available", "cash")
            if balance is not None:
                instance.account.initial_balance = balance
                instance.account.equity = balance
            if available is not None:
                instance.account.available_cash = available
            instance.account.provider = provider
            await self._persist_instance(instance)

    def queue_size(self, trading_id: str) -> int:
        return self._get_existing_instance(trading_id).queue.qsize()

    def _update_market_state(self, event: dict[str, Any]) -> None:
        outcome = str(event.get("outcome_key") or "")
        guid = str(event.get("guid") or "")
        if outcome not in {"home", "draw", "away"} or not guid:
            return
        row = self._market_state.setdefault(guid, {}).setdefault(outcome, {"ask1": None, "bid1": None})
        if event.get("ask1") is not None:
            row["ask1"] = _optional_number(event.get("ask1"))
        if event.get("bid1") is not None:
            row["bid1"] = _optional_number(event.get("bid1"))

    async def process_queued_events(self) -> dict[str, int]:
        processed = 0
        trades = 0
        failures = 0
        for instance in list(self._tradings.values()):
            while not instance.queue.empty():
                event = instance.queue.get_nowait()
                processed += 1
                try:
                    new_trades = await self._process_event(instance, event)
                    trades += new_trades
                except Exception as exc:
                    failures += 1
                    instance.logs.append(
                        {
                            "level": "error",
                            "source": "trader",
                            "guid": event.get("guid"),
                            "trader_id": instance.snapshot.trading_id,
                            "event_type": "strategy_error",
                            "message": str(exc),
                            "ts_utc": _utc_now(),
                        }
                    )
                    await self._persist_instance(instance)
        return {"processed": processed, "trades": trades, "failures": failures}

    async def _run_loop(self) -> None:
        while self._running:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake_event.wait(), timeout=self._process_interval_seconds)
            self._wake_event.clear()
            with suppress(Exception):
                await self.process_queued_events()

    async def persist(self, trading_id: str) -> None:
        await self._persist_instance(self._get_existing_instance(trading_id))

    async def get_pm_match(self, guid: str) -> dict[str, Any] | None:
        if self._store is None:
            return None
        return await self._store.get_json(f"pm:match:{guid}")

    async def get_gs_match(self, guid: str) -> dict[str, Any] | None:
        if self._store is None:
            return None
        return (
            await self._store.get_json(f"gs:match:{guid}")
            or await self._store.get_json(f"asa:match:{guid}")
            or await self._store.get_json(f"external:match:{guid}")
        )

    def _get_existing_instance(self, trading_id: str) -> TraderInstance:
        if trading_id in self._tradings:
            return self._tradings[trading_id]
        raise KeyError(f"trading {trading_id} not found")

    async def _persist_instance(self, instance: TraderInstance) -> None:
        if self._store is None:
            return
        trading_id = instance.snapshot.trading_id
        await self._store.set_text("idx:traders", trading_id)
        await self._store.set_json(f"trader:{trading_id}:config", instance.snapshot.model_dump())
        await self._store.set_json(
            f"trader:{trading_id}:state",
            {"status": instance.snapshot.status, "mode": instance.snapshot.mode},
        )
        await self._store.set_json(f"trader:{trading_id}:account", asdict(instance.account))
        await self._store.set_json(
            f"trader:{trading_id}:positions",
            [dict(asdict(position), trading_id=trading_id) for position in instance.positions],
        )
        await self._append_instance_rows(instance, "trades")
        await self._append_instance_rows(instance, "logs")

    async def _append_instance_rows(self, instance: TraderInstance, kind: str) -> None:
        if self._store is None:
            return
        trading_id = instance.snapshot.trading_id
        rows = instance.trades if kind == "trades" else instance.logs
        persisted_attr = "persisted_trade_count" if kind == "trades" else "persisted_log_count"
        start = int(getattr(instance, persisted_attr))
        for row in rows[start:]:
            if isinstance(row, dict):
                await self._store.add_stream(
                    f"stream:trader:{trading_id}:{kind}",
                    {**row, "trading_id": trading_id},
                    max_len=None,
                )
        setattr(instance, persisted_attr, len(rows))

    async def _hydrate_from_store(self) -> None:
        if self._store is None:
            return
        for key in await self._store.keys("trader:*:config"):
            snapshot_row = await self._store.get_json(key)
            if not snapshot_row:
                continue
            snapshot = TradingSnapshot(**snapshot_row)
            trading_id = snapshot.trading_id
            state = await self._store.get_json(f"trader:{trading_id}:state") or {}
            if state.get("status") in {"running", "stopped"}:
                snapshot = snapshot.model_copy(update={"status": state["status"]})
            account_row = await self._store.get_json(f"trader:{trading_id}:account") or {}
            account = Account(**_dataclass_kwargs(Account, account_row)) if account_row else Account(
                trading_id=trading_id,
                mode=snapshot.mode,
                account_alias=None,
                initial_balance=float(snapshot.strategy_params.get("initial_balance", 10000.0)),
                available_cash=float(snapshot.strategy_params.get("initial_balance", 10000.0)),
            )
            positions_row = await self._store.get_json(f"trader:{trading_id}:positions") or []
            positions = [
                Position(**_dataclass_kwargs(Position, row))
                for row in positions_row
                if isinstance(row, dict)
            ]
            legacy_trades = await self._store.get_json(f"trader:{trading_id}:trades") or []
            stream_trades = await self._store.stream(f"stream:trader:{trading_id}:trades")
            trades = [
                row
                for row in [*(legacy_trades if isinstance(legacy_trades, list) else []), *stream_trades]
                if isinstance(row, dict)
            ]
            legacy_logs = await self._store.get_json(f"trader:{trading_id}:logs") or []
            stream_logs = await self._store.stream(f"stream:trader:{trading_id}:logs")
            logs = [
                row
                for row in [*(legacy_logs if isinstance(legacy_logs, list) else []), *stream_logs]
                if isinstance(row, dict)
            ]
            self._tradings[trading_id] = TraderInstance(
                snapshot=snapshot,
                account=account,
                risk=_risk_from_params(snapshot.strategy_params or {}),
                positions=positions,
                trades=trades,
                logs=logs,
                account_alias=account.account_alias,
                persisted_trade_count=len(trades),
                persisted_log_count=len(logs),
            )

    async def _process_event(self, instance: TraderInstance, event: dict[str, Any]) -> int:
        guid = event.get("guid")
        if not guid:
            return 0
        api = self.api(instance.snapshot.trading_id)
        pm = await api.get_pm_match(guid) or {}
        gs = await api.get_gs_match(guid) or {}
        common_exit_intents = await _position_exit_intents(api, guid, pm, gs)
        if common_exit_intents:
            intents = common_exit_intents
        elif instance.snapshot.strategy_name == SCORE_DELAY_STRATEGY:
            intents = await football_score_delay_trade(api, guid, event)
        elif instance.snapshot.strategy_name == WINRATE_GAP_STRATEGY:
            intents = await football_winrate_gap_buy(api, guid)
        else:
            return 0
        return await self._execute_intents(instance, intents)

    async def _process_market_tick(self, instance: TraderInstance, event: dict[str, Any]) -> int:
        guid = str(event.get("guid") or "")
        if not guid:
            return 0
        api = self.api(instance.snapshot.trading_id)
        pm = await api.get_pm_match(guid) or {}
        gs = await api.get_gs_match(guid) or {}
        intents = await _position_exit_intents(api, guid, pm, gs)
        trades = await self._execute_intents(instance, intents)
        # 即使没有触发卖出，tick 也可能更新持仓 peak/current 估值，需要保留到内存/Redis 快照。
        await self._persist_instance(instance)
        return trades

    async def _execute_intents(self, instance: TraderInstance, intents: list[dict[str, Any]]) -> int:
        api = self.api(instance.snapshot.trading_id)
        trades = 0
        for intent in intents:
            if intent["action"] == "buy":
                await api.buy(intent["guid"], intent["outcome_key"], intent["amount_usd"], intent.get("reason"))
                trades += 1
            elif intent["action"] == "sell":
                await api.sell(intent["guid"], intent["outcome_key"], intent["shares"], intent.get("reason"))
                trades += 1
            elif intent["action"] == "log":
                await api.log_runtime(str(intent.get("reason") or "未交易"), intent)
        await self._persist_instance(instance)
        return trades


class TraderAPI:
    def __init__(self, manager: TraderManager, trading_id: str):
        self._manager = manager
        self._trading_id = trading_id

    @property
    def trading_id(self) -> str:
        return self._trading_id

    async def get_market(self, guid: str) -> dict[str, dict[str, float | None]]:
        pm = await self.get_pm_match(guid) or {}
        cached = self._manager._market_state.get(guid, {})
        return {
            outcome: {
                "ask1": cached.get(outcome, {}).get("ask1")
                if cached.get(outcome, {}).get("ask1") is not None
                else pm.get(f"{outcome}_ask1"),
                "bid1": cached.get(outcome, {}).get("bid1")
                if cached.get(outcome, {}).get("bid1") is not None
                else pm.get(f"{outcome}_bid1"),
            }
            for outcome in ("home", "draw", "away")
        }

    async def get_pm_match(self, guid: str) -> dict[str, Any] | None:
        return await self._manager.get_pm_match(guid)

    async def get_gs_match(self, guid: str) -> dict[str, Any] | None:
        return await self._manager.get_gs_match(guid)

    def get_positions(self) -> list[Position]:
        return self._manager.get_positions(self._trading_id)

    def get_balance(self) -> float:
        return self._manager.get_account(self._trading_id).available_cash

    async def get_assets(self) -> list[Position]:
        return self.get_positions()

    async def log_trade(self, message: str, payload: dict[str, Any] | None = None) -> None:
        self._instance.logs.append(
            {
                "level": "info",
                "source": "trader",
                "guid": (payload or {}).get("guid"),
                "trader_id": self._trading_id,
                "order_id": (payload or {}).get("order_id"),
                "event_type": "trade_log",
                "message": message,
                "payload": payload or {},
                "ts_utc": _utc_now(),
            }
        )
        await self._manager.persist(self._trading_id)

    async def log_runtime(self, message: str, payload: dict[str, Any] | None = None) -> None:
        self._instance.logs.append(
            {
                "level": "info",
                "source": "trader",
                "guid": (payload or {}).get("guid"),
                "trader_id": self._trading_id,
                "order_id": (payload or {}).get("order_id"),
                "event_type": "runtime_log",
                "message": message,
                "payload": payload or {},
                "ts_utc": _utc_now(),
            }
        )
        await self._manager.persist(self._trading_id)

    async def buy(
        self,
        guid: str,
        outcome_key: str,
        amount_usd: float,
        reason: str | None = None,
    ) -> dict[str, Any]:
        instance = self._instance
        quote = await self._quote(guid, outcome_key, log_difference=True)
        ask = quote.get("ask1")
        if ask is None:
            raise ValueError("buy requires ask1")
        # amount_usd 是美元预算；按最新 ask1 转成整数份数，最终成本不超过预算。
        shares = max(1, int(float(amount_usd) / float(ask)))
        cost = shares * float(ask)
        _validate_buy(instance, guid, outcome_key, cost)
        order_id = _new_order_id()
        context = await self._trade_context(guid, outcome_key)
        if instance.snapshot.mode == "real":
            # 真实交易模式目前仍强制 dry-run，避免任何未授权的真实下单。
            result = {
                "order_id": order_id,
                "side": "buy",
                "guid": guid,
                **context,
                "outcome_key": outcome_key,
                "amount_usd": cost,
                "price": ask,
                "shares": shares,
                "reason": reason or "",
                "dry_run": True,
                "submitted": False,
            }
            instance.trades.append(dict(result, ts_utc=_utc_now()))
            _append_trader_log(instance, "buy", guid, _trade_log_message("买入", outcome_key, shares, float(ask), cost, reason), result)
            await self._manager.persist(self._trading_id)
            return result
        account = instance.account
        account.available_cash -= cost
        position = _find_position(instance, guid, outcome_key)
        if position is None:
            position = Position(
                guid=guid,
                outcome_key=outcome_key,
                shares=shares,
                avg_entry_price=float(ask),
                cost_basis=cost,
                order_id=order_id,
                slug=context["slug"],
                team_label=context["team_label"],
                team_name=context["team_name"],
                current_ask1=float(ask),
                current_bid1=quote.get("bid1"),
                peak_value=cost,
                peak_price=float(ask),
            )
            instance.positions.append(position)
        else:
            total_cost = position.cost_basis + cost
            position.shares += shares
            position.avg_entry_price = total_cost / position.shares
            position.cost_basis = total_cost
            position.add_count += 1
            position.slug = position.slug or context["slug"]
            position.team_label = position.team_label or context["team_label"]
            position.team_name = position.team_name or context["team_name"]
            position.current_ask1 = float(ask)
            position.peak_price = max(position.peak_price or 0.0, position.avg_entry_price, float(ask))
        _refresh_account(instance)
        trade = {
            "order_id": order_id,
            "side": "buy",
            "guid": guid,
            **context,
            "outcome_key": outcome_key,
            "amount_usd": cost,
            "price": float(ask),
            "shares": shares,
            "reason": reason or "",
            "ts_utc": _utc_now(),
        }
        instance.trades.append(trade)
        _append_trader_log(instance, "buy", guid, _trade_log_message("买入", outcome_key, shares, float(ask), cost, reason), trade)
        await self._manager.persist(self._trading_id)
        return trade

    async def sell(
        self,
        guid: str,
        outcome_key: str,
        shares: float,
        reason: str | None = None,
    ) -> dict[str, Any]:
        instance = self._instance
        quote = await self._quote(guid, outcome_key, log_difference=True)
        bid = quote.get("bid1")
        if bid is None:
            raise ValueError("sell requires bid1")
        position = _find_position(instance, guid, outcome_key)
        sell_shares = max(1, int(float(shares)))
        if position is None or int(position.shares) < sell_shares:
            raise ValueError("sell requires available position")
        cost_basis_sold = min(position.cost_basis, sell_shares * position.avg_entry_price)
        order_id = _new_order_id()
        context = await self._trade_context(guid, outcome_key, position)
        if instance.snapshot.mode == "real":
            # 真实卖出同样只返回审计结果，不触达 PM 下单接口。
            proceeds = sell_shares * float(bid)
            profit = proceeds - cost_basis_sold
            result = {
                "order_id": order_id,
                "side": "sell",
                "guid": guid,
                **context,
                "outcome_key": outcome_key,
                "shares": sell_shares,
                "price": bid,
                "amount_usd": proceeds,
                "cost_basis": cost_basis_sold,
                "profit": profit,
                "profit_rate": 0.0 if cost_basis_sold <= 0 else profit / cost_basis_sold * 100,
                "reason": reason or "",
                "dry_run": True,
                "submitted": False,
            }
            instance.trades.append(dict(result, ts_utc=_utc_now()))
            _append_trader_log(instance, "sell", guid, _trade_log_message("卖出", outcome_key, sell_shares, float(bid), proceeds, reason, profit), result)
            await self._manager.persist(self._trading_id)
            return result
        proceeds = sell_shares * float(bid)
        profit = proceeds - cost_basis_sold
        position.shares -= sell_shares
        position.cost_basis = max(0.0, position.cost_basis - cost_basis_sold)
        position.current_bid1 = float(bid)
        if position.shares < 1:
            instance.positions.remove(position)
        instance.account.available_cash += proceeds
        _refresh_account(instance)
        trade = {
            "order_id": order_id,
            "side": "sell",
            "guid": guid,
            **context,
            "outcome_key": outcome_key,
            "shares": sell_shares,
            "price": float(bid),
            "amount_usd": proceeds,
            "cost_basis": cost_basis_sold,
            "profit": profit,
            "profit_rate": 0.0 if cost_basis_sold <= 0 else profit / cost_basis_sold * 100,
            "reason": reason or "",
            "ts_utc": _utc_now(),
        }
        instance.trades.append(trade)
        _append_trader_log(instance, "sell", guid, _trade_log_message("卖出", outcome_key, sell_shares, float(bid), proceeds, reason, profit), trade)
        await self._manager.persist(self._trading_id)
        return trade

    async def _quote(
        self,
        guid: str,
        outcome_key: str,
        *,
        use_clob: bool = True,
        log_difference: bool = False,
    ) -> dict[str, float | None]:
        market = await self.get_market(guid)
        cached = dict(market.get(outcome_key, {}))
        if not use_clob or self._manager._clob_quote_client is None:
            return cached
        pm = await self.get_pm_match(guid) or {}
        asset_id = str(pm.get(f"{outcome_key}_asset_id") or "").strip()
        if not asset_id:
            return cached
        try:
            fresh_raw = await self._manager._clob_quote_client.get_quote(asset_id)
        except Exception as exc:
            if log_difference:
                _append_trader_log(
                    self._instance,
                    "quote_warning",
                    guid,
                    f"CLOB报价查询失败，使用WS报价：{_safe_error(exc)}",
                    {"guid": guid, "outcome_key": outcome_key},
                )
            return cached
        fresh = {
            "ask1": _optional_number((fresh_raw or {}).get("ask1") or (fresh_raw or {}).get("ask")),
            "bid1": _optional_number((fresh_raw or {}).get("bid1") or (fresh_raw or {}).get("bid")),
        }
        if log_difference:
            _append_quote_difference_log(self._instance, guid, outcome_key, cached, fresh)
        return {
            "ask1": _max_optional_number(cached.get("ask1"), fresh["ask1"]),
            "bid1": _min_optional_number(cached.get("bid1"), fresh["bid1"]),
        }

    async def _trade_context(
        self,
        guid: str,
        outcome_key: str,
        position: Position | None = None,
    ) -> dict[str, str]:
        pm = await self.get_pm_match(guid) or {}
        team_label, team_name = _outcome_display(pm, outcome_key)
        return {
            "slug": str(pm.get("slug") or (position.slug if position else "") or guid),
            "team_label": position.team_label if position and position.team_label else team_label,
            "team_name": position.team_name if position and position.team_name else team_name,
        }

    @property
    def _instance(self) -> TraderInstance:
        return self._manager._get_existing_instance(self._trading_id)


async def football_score_delay_trade(
    api: TraderAPI,
    guid: str,
    event: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not _is_external_score_change_event(event):
        return []
    pm = await api.get_pm_match(guid) or {}
    gs = await api.get_gs_match(guid) or {}
    pm_home = _score_int(event.get("pm_score_home_at_event"), pm.get("score_home"))
    pm_away = _score_int(event.get("pm_score_away_at_event"), pm.get("score_away"))
    gs_home = _score_int(event.get("score_home"), gs.get("score_home"))
    gs_away = _score_int(event.get("score_away"), gs.get("score_away"))
    positions = [position for position in api.get_positions() if position.guid == guid]
    protective_exit = await _score_delay_protective_exit(api, guid, event)
    if protective_exit:
        return protective_exit
    if (gs_home, gs_away) == (pm_home, pm_away):
        return []

    target = _score_delay_target(pm_home, pm_away, gs_home, gs_away, event)
    if target is None:
        reason = _score_delay_no_target_reason(event)
        if reason:
            return [_no_trade_intent(guid, reason)]
        return []

    current_minute = _minute(event.get("clock") or event.get("match_time") or gs.get("clock") or gs.get("match_time") or pm.get("match_time"))
    opposite = [position for position in positions if position.outcome_key != target and position.shares > 0]

    market = await api.get_market(guid)
    ask = market[target].get("ask1")
    same = [position for position in positions if position.outcome_key == target and position.shares > 0]
    is_add = bool(same)
    if ask is None:
        return [_no_trade_intent(guid, "缺少ask1不买入")]

    intents: list[dict[str, Any]] = []
    for position in opposite:
        intents.append(
            {
                "action": "sell",
                "guid": guid,
                "outcome_key": position.outcome_key,
                "shares": int(position.shares),
                "reason": "反向进球卖出",
            }
        )
    signal_name = "追平" if target == "draw" else "进球"
    if float(ask) > 0.93:
        return intents or [_no_trade_intent(guid, f"{signal_name}但高于0.93不加仓" if is_add else f"{signal_name}但高于0.93不买入")]
    if same and not _score_delay_signal_advanced(event, target):
        return intents or [_no_trade_intent(guid, "未再次追平不加仓" if target == "draw" else "未继续进球不加仓")]
    if same and same[0].add_count >= api._instance.risk.max_add_count:
        return intents or [_no_trade_intent(guid, "单场最多加仓次数已达上限不加仓")]
    budget = _entry_budget(api, is_add=is_add, current_minute=current_minute)
    if budget <= 0:
        return intents or [_no_trade_intent(guid, "单次加仓资金超限不加仓" if is_add else "单笔资金超限不买入")]
    intents.append(
        {
            "action": "buy",
            "guid": guid,
            "outcome_key": target,
            "amount_usd": budget,
            "reason": f"{signal_name}加仓" if same else f"{signal_name}买入",
        }
    )
    return intents


async def football_winrate_gap_buy(api: TraderAPI, guid: str) -> list[dict[str, Any]]:
    """胜率差买入策略。

    这里的“胜率”只使用 PM moneyline 的 ask1 近似，策略不推导、不补造比分或时间。
    入场窗口用 PM 已有开赛时间判断；已有持仓时只检查回撤/完场退出，不重复买入。
    """

    pm = await api.get_pm_match(guid) or {}
    gs = await api.get_gs_match(guid) or {}
    exit_intents = await _position_exit_intents(api, guid, pm, gs)
    if exit_intents:
        return exit_intents

    positions = [position for position in api.get_positions() if position.guid == guid and position.shares > 0]
    if positions:
        return []
    if any(trade.get("guid") == guid and trade.get("side") == "sell" for trade in api._instance.trades):
        return []
    params = api._instance.snapshot.strategy_params or {}
    if not _entry_window_open(pm, gs, params):
        return []

    gap = _winrate_gap_target(pm, params)
    if gap is None:
        return []
    target, _gap_value = gap
    return [
        {
            "action": "buy",
            "guid": guid,
            "outcome_key": target,
            "amount_usd": _entry_budget(api, is_add=False, current_minute=0),
            "reason": "胜率差买入",
        }
    ]


def _risk_from_params(params: dict[str, Any]) -> RiskLimits:
    raw = params.get("risk") if isinstance(params.get("risk"), dict) else params
    return RiskLimits(
        max_positions=int(raw.get("max_positions", 3)),
        max_fund_usage_pct=float(raw.get("max_fund_usage_pct", 80.0)),
        max_single_order_pct=float(
            raw.get("max_single_order_pct", raw.get("max_single_order_usd_pct", 20.0))
        ),
        max_add_count=int(raw.get("max_add_count", 2)),
        max_add_fund_pct=float(raw.get("max_add_fund_pct", 10.0)),
        stop_loss_drawdown=float(raw.get("stop_loss_drawdown", 0.05)),
    )


def _is_external_score_change_event(event: dict[str, Any] | None) -> bool:
    if not event:
        return False
    if event.get("source") not in {"gs_live", "asa_live"}:
        return False
    changed = set(event.get("changed_fields") or [])
    return bool({"score_home", "score_away"} & changed)


def _leader_scored_again(event: dict[str, Any] | None, target: str) -> bool:
    if not event:
        return False
    current_field = "score_home" if target == "home" else "score_away"
    previous_field = "previous_score_home" if target == "home" else "previous_score_away"
    current = _optional_number(event.get(current_field))
    previous = _optional_number(event.get(previous_field))
    if current is None or previous is None:
        return False
    return current > previous


def _score_int(*values: Any) -> int:
    for value in values:
        number = _optional_number(value)
        if number is not None:
            return int(number)
    return 0


def _score_delay_target(
    pm_home: int,
    pm_away: int,
    gs_home: int,
    gs_away: int,
    event: dict[str, Any] | None,
) -> str | None:
    if _external_equalized_before_pm(pm_home, pm_away, event):
        return "draw"
    if gs_home > gs_away and gs_home > pm_home:
        return "home"
    if gs_away > gs_home and gs_away > pm_away:
        return "away"
    return None


def _score_delay_signal_advanced(event: dict[str, Any] | None, target: str) -> bool:
    if target == "draw":
        return _external_equalized(event)
    return _leader_scored_again(event, target)


def _score_delay_no_target_reason(event: dict[str, Any] | None) -> str | None:
    if not event:
        return None
    previous_home = _optional_number(event.get("previous_score_home"))
    previous_away = _optional_number(event.get("previous_score_away"))
    score_home = _optional_number(event.get("score_home"))
    score_away = _optional_number(event.get("score_away"))
    if None in {previous_home, previous_away, score_home, score_away}:
        return None
    previous = (int(previous_home), int(previous_away))
    current = (int(score_home), int(score_away))
    if sum(current) <= sum(previous):
        return None
    previous_leader = _leader_from_score(*previous)
    current_leader = _leader_from_score(*current)
    if previous_leader and previous_leader == current_leader:
        previous_margin = abs(previous[0] - previous[1])
        current_margin = abs(current[0] - current[1])
        if previous_margin > current_margin > 1:
            return "落后方进球但领先优势仍大于1不操作"
    return None


def _external_equalized_before_pm(pm_home: int, pm_away: int, event: dict[str, Any] | None) -> bool:
    return pm_home != pm_away and _external_equalized(event)


def _external_equalized(event: dict[str, Any] | None) -> bool:
    if not event:
        return False
    previous_home = _optional_number(event.get("previous_score_home"))
    previous_away = _optional_number(event.get("previous_score_away"))
    score_home = _optional_number(event.get("score_home"))
    score_away = _optional_number(event.get("score_away"))
    if None in {previous_home, previous_away, score_home, score_away}:
        return False
    previous = (int(previous_home), int(previous_away))
    current = (int(score_home), int(score_away))
    return previous[0] != previous[1] and current[0] == current[1] and sum(current) > sum(previous)


def _entry_budget(api: TraderAPI, *, is_add: bool, current_minute: int) -> float:
    """按通用参数计算本次买入美元预算；85 分钟后自动减半。"""
    account = api._instance.account
    risk = api._instance.risk
    pct = risk.max_add_fund_pct if is_add else risk.max_single_order_pct
    budget = account.equity * pct / 100
    if current_minute >= 85:
        budget *= 0.5
    if is_add:
        budget = min(budget, account.equity * risk.max_add_fund_pct / 100)
    else:
        budget = min(budget, account.equity * risk.max_single_order_pct / 100)
    return max(0.0, budget)


async def _score_delay_protective_exit(
    api: TraderAPI,
    guid: str,
    event: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not event:
        return []
    previous_home = _optional_number(event.get("previous_score_home"))
    previous_away = _optional_number(event.get("previous_score_away"))
    score_home = _optional_number(event.get("score_home"))
    score_away = _optional_number(event.get("score_away"))
    if None in {previous_home, previous_away, score_home, score_away}:
        return []
    previous = (int(previous_home), int(previous_away))
    current = (int(score_home), int(score_away))
    previous_leader = _leader_from_score(*previous)
    current_leader = _leader_from_score(*current)
    if previous_leader is None or previous_leader != current_leader:
        return []
    previous_margin = abs(previous[0] - previous[1])
    current_margin = abs(current[0] - current[1])
    if previous_margin != 2 or current_margin != 1:
        return []
    market = await api.get_market(guid)
    intents: list[dict[str, Any]] = []
    for position in [item for item in api.get_positions() if item.guid == guid and item.outcome_key == current_leader]:
        bid = _optional_number(market.get(position.outcome_key, {}).get("bid1"))
        if bid is None:
            continue
        profit = position.shares * bid - position.cost_basis
        if bid > 0.85 and profit > 0:
            intents.append(
                {
                    "action": "sell",
                    "guid": guid,
                    "outcome_key": position.outcome_key,
                    "shares": int(position.shares),
                    "reason": "领先优势缩小平仓",
                }
            )
    return intents


def _leader_from_score(home: int, away: int) -> str | None:
    if home > away:
        return "home"
    if away > home:
        return "away"
    return None


def _dataclass_kwargs(model: type[Any], row: dict[str, Any]) -> dict[str, Any]:
    allowed = {field.name for field in dataclass_fields(model)}
    return {key: value for key, value in row.items() if key in allowed}


def _trading_id_number(trading_id: str, mode: str) -> int | None:
    prefix = "R" if mode == "real" else "S"
    matched = re.match(rf"^{prefix}(\d+)$", trading_id, flags=re.IGNORECASE)
    if matched:
        return int(matched.group(1))
    return None


def _validate_scope(strategy_name: str, affect_sports: list[str]) -> None:
    if strategy_name not in SUPPORTED_STRATEGIES:
        raise ValueError(f"unsupported strategy: {strategy_name}")
    if affect_sports != SUPPORTED_SPORTS:
        raise ValueError("only football affect_sports is supported")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _new_order_id() -> str:
    return uuid.uuid4().hex[:16]


def _outcome_display(pm: dict[str, Any], outcome_key: str) -> tuple[str, str]:
    if outcome_key == "home":
        return "Home", str(pm.get("home_team") or pm.get("home") or "")
    if outcome_key == "away":
        return "Away", str(pm.get("away_team") or pm.get("away") or "")
    if outcome_key == "draw":
        return "Draw", ""
    return outcome_key, ""


def _trade_log_message(
    action_label: str,
    outcome_key: str,
    shares: int,
    price: float,
    amount: float,
    reason: str | None = None,
    profit: float | None = None,
) -> str:
    reason_part = f"，原因：{reason}" if reason else ""
    if profit is None:
        return f"{action_label} {outcome_key} {shares} 份 @ {price:.3f}，金额 ${amount:.2f}{reason_part}"
    return f"{action_label} {outcome_key} {shares} 份 @ {price:.3f}，收益 ${profit:.2f}{reason_part}"


def _format_reason_number(value: float) -> str:
    return f"{value:g}"


def _validate_buy(instance: TraderInstance, guid: str, outcome_key: str, amount_usd: float) -> None:
    account = instance.account
    risk = instance.risk
    existing = _find_position(instance, guid, outcome_key)
    if existing is None and len(instance.positions) >= risk.max_positions:
        raise ValueError("max_positions exceeded")
    if amount_usd > account.available_cash:
        raise ValueError("insufficient available_cash")
    if amount_usd > account.equity * risk.max_single_order_pct / 100:
        raise ValueError("max_single_order_pct exceeded")
    used = sum(position.cost_basis for position in instance.positions)
    if used + amount_usd > account.equity * risk.max_fund_usage_pct / 100:
        raise ValueError("max_fund_usage_pct exceeded")
    if existing is not None and existing.add_count >= risk.max_add_count:
        raise ValueError("max_add_count exceeded")
    if existing is not None and amount_usd > account.equity * risk.max_add_fund_pct / 100:
        raise ValueError("max_add_fund_pct exceeded")


def _find_position(instance: TraderInstance, guid: str, outcome_key: str) -> Position | None:
    for position in instance.positions:
        if position.guid == guid and position.outcome_key == outcome_key:
            return position
    return None


def _append_trader_log(
    instance: TraderInstance,
    event_type: str,
    guid: str,
    message: str,
    payload: dict[str, Any],
) -> None:
    instance.logs.append(
        {
            "level": "info",
            "source": "trader",
            "guid": guid,
            "trader_id": instance.snapshot.trading_id,
            "order_id": payload.get("order_id"),
            "event_type": event_type,
            "message": message,
            "payload": payload,
            "ts_utc": payload.get("ts_utc") or _utc_now(),
        }
    )


def _append_quote_difference_log(
    instance: TraderInstance,
    guid: str,
    outcome_key: str,
    cached: dict[str, Any],
    fresh: dict[str, Any],
) -> None:
    # 交易前用 CLOB 最新盘口覆盖 WS 缓存；差异大时留日志，方便回放判断是否 WS 滞后。
    parts: list[str] = []
    for label in ("ask1", "bid1"):
        old = _optional_number(cached.get(label))
        new = _optional_number(fresh.get(label))
        if old is None or new is None:
            continue
        if round(abs(new - old), 6) > 0.01:
            parts.append(f"{label} WS {old:.3f} -> CLOB {new:.3f}")
    if not parts:
        return
    _append_trader_log(
        instance,
        "quote_warning",
        guid,
        f"CLOB报价与WS差异超过0.01：{outcome_key} " + "，".join(parts),
        {"guid": guid, "outcome_key": outcome_key, "cached": cached, "fresh": fresh},
    )


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:160]


def _refresh_account(instance: TraderInstance) -> None:
    account = instance.account
    used = sum(position.cost_basis for position in instance.positions)
    marked_value = 0.0
    for position in instance.positions:
        if position.current_bid1 is not None:
            marked_value += position.shares * float(position.current_bid1)
        else:
            marked_value += position.cost_basis
    account.equity = account.available_cash + marked_value
    account.position_count = len(instance.positions)
    account.fund_usage_pct = 0.0 if account.equity == 0 else used / account.equity * 100


def _minute(value: Any) -> int:
    match = re.match(r"^\s*(\d{1,3})", str(value or ""))
    return int(match.group(1)) if match else 0


def _minute_or_none(value: Any) -> int | None:
    match = re.match(r"^\s*(\d{1,3})", str(value or ""))
    return int(match.group(1)) if match else None


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_optional_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key not in row:
            continue
        number = _optional_number(row.get(key))
        if number is not None:
            return number
    return None


def _max_optional_number(left: Any, right: Any) -> float | None:
    values = [value for value in (_optional_number(left), _optional_number(right)) if value is not None]
    return max(values) if values else None


def _min_optional_number(left: Any, right: Any) -> float | None:
    values = [value for value in (_optional_number(left), _optional_number(right)) if value is not None]
    return min(values) if values else None


def _utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _strategy_now(params: dict[str, Any]) -> datetime:
    configured = _utc_datetime(params.get("now_utc"))
    return configured or datetime.now(UTC)


def _ratio_param(params: dict[str, Any], key: str, default_pct: float) -> float:
    raw = _optional_number(params.get(key))
    value = default_pct if raw is None else raw
    return value / 100 if value > 1 else value


def _score_pair(pm: dict[str, Any], gs: dict[str, Any]) -> tuple[int, int]:
    source = gs if "score_home" in gs or "score_away" in gs else pm
    return int(source.get("score_home") or 0), int(source.get("score_away") or 0)


def _is_finished(pm: dict[str, Any], gs: dict[str, Any]) -> bool:
    final_statuses = {"finished", "final", "ended", "closed", "resolved", "complete", "completed", "ft", "fulltime"}
    return any(str(source.get("status") or "").strip().lower() in final_statuses for source in (pm, gs))


def _entry_window_open(pm: dict[str, Any], gs: dict[str, Any], params: dict[str, Any]) -> bool:
    if _is_finished(pm, gs):
        return False
    start = _utc_datetime(pm.get("start_time_utc"))
    if start is None:
        return False
    now = _strategy_now(params)
    before_minutes = float(params.get("entry_before_minutes", 5.0))
    after_minutes = float(params.get("entry_after_minutes", 15.0))
    if now < start:
        return 0 <= (start - now).total_seconds() / 60 <= before_minutes

    if _score_pair(pm, gs) != (0, 0):
        return False
    current_minute = _minute_or_none(gs.get("clock") or gs.get("match_time") or pm.get("match_time"))
    elapsed_minutes = float(current_minute) if current_minute is not None else (now - start).total_seconds() / 60
    return 0 <= elapsed_minutes <= after_minutes


def _winrate_gap_target(pm: dict[str, Any], params: dict[str, Any]) -> tuple[str, float] | None:
    home = _optional_number(pm.get("home_ask1"))
    away = _optional_number(pm.get("away_ask1"))
    if home is None or away is None:
        return None
    threshold = _ratio_param(params, "winrate_gap_pct", 30.0)
    if home - away >= threshold:
        return "home", home - away
    if away - home >= threshold:
        return "away", away - home
    return None


def _no_trade_intent(guid: str, reason: str) -> dict[str, Any]:
    return {"action": "log", "guid": guid, "reason": reason}


async def _position_exit_intents(
    api: TraderAPI,
    guid: str,
    pm: dict[str, Any],
    gs: dict[str, Any],
) -> list[dict[str, Any]]:
    intents: list[dict[str, Any]] = []
    finished = _is_finished(pm, gs)
    drawdown = max(0.0, float(api._instance.risk.stop_loss_drawdown))
    for position in [item for item in api.get_positions() if item.guid == guid and item.shares > 0]:
        quote = await api._quote(guid, position.outcome_key, log_difference=False)
        bid = _optional_number(quote.get("bid1"))
        ask = _optional_number(quote.get("ask1"))
        if bid is None or ask is None:
            continue
        current_value = position.shares * bid
        position.current_bid1 = bid
        position.current_ask1 = ask
        position.unrealized_pnl = current_value - position.cost_basis
        position.peak_value = max(position.peak_value or 0.0, position.cost_basis, current_value)
        # stop_loss_drawdown 是 ask1 的绝对回撤，例如 peak ask1 从 0.65 回到 0.60。
        position.peak_price = max(position.peak_price or 0.0, position.avg_entry_price, ask)
        hit_drawdown = drawdown > 0 and (position.peak_price - ask) >= drawdown
        if finished or hit_drawdown:
            reason = "比赛结束卖出" if finished else f"回撤{_format_reason_number(drawdown)}卖出"
            intents.append(
                {
                    "action": "sell",
                    "guid": guid,
                    "outcome_key": position.outcome_key,
                    "shares": int(position.shares),
                    "reason": reason,
                }
            )
    return intents
