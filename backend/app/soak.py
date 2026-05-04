"""dry-run soak：用 fixture 链路验证 Collector -> Listener -> Trader，不提交真实订单。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DryRunSoakRunner:
    store: Any
    collector: Any
    listener: Any
    trader_manager: Any

    async def run(self, iterations: int = 1, cleanup_trader: bool = True) -> dict[str, Any]:
        report = await self.collector.collect_once()
        if not report["bindings"]:
            return {
                "dry_run": True,
                "matched": 0,
                "events_processed": 0,
                "trades": 0,
                "failures": 0,
                "real_submissions": 0,
            }
        trading = await self.trader_manager.create_trading(
            {
                "strategy_name": "football_score_delay_trade",
                "strategy_params": {"initial_balance": 1000.0, "stake_usd": 100.0},
                "affect_sports": ["football"],
                "mode": "simulation",
            }
        )
        try:
            await self.trader_manager.start_trading(trading.trading_id)
            binding = report["bindings"][0]
            total_processed = 0
            total_trades = 0
            failures = 0
            for index in range(iterations):
                await self.listener.process_payload(
                    "gs_live",
                    {
                        "inplay_id": binding["gs_inplay_id"],
                        "score": {"home": 1, "away": 0},
                        "clock": f"34:{index:02d}",
                        "period": "1H",
                        "message_id": f"soak-gs-{index}",
                    },
                )
                processed = await self.trader_manager.process_queued_events()
                total_processed += processed["processed"]
                total_trades += processed["trades"]
                failures += processed["failures"]
            return {
                "dry_run": True,
                "matched": report["matched"],
                "events_processed": total_processed,
                "trades": total_trades,
                "failures": failures,
                "real_submissions": 0,
            }
        finally:
            if cleanup_trader:
                await self.trader_manager.delete_trading(trading.trading_id)
