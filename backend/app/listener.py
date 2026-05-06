"""Listener：负责 WS 消息接收、过滤、标准化、入库和实时广播。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .allsportsapi import normalize_allsportsapi_ws_payload
from .discriminator import MatchDiscriminator
from .goalserve import normalize_goalserve_ws_payload
from .timeseries import CURRENT_STATE_TTL_SECONDS, MATCH_RELATED_TTL_SECONDS, append_pm_tick_snapshot, normalize_ts


PM_SOURCE_NAMES = {"pm_sports", "pm_market", "pm_user"}
GS_SOURCE_NAMES = {"gs_live"}
ASA_SOURCE_NAMES = {"asa_live"}
CONNECTION_STATUS_FIELD = "__connection_status__"
CONNECTION_STATUS_CONNECTED = "connected"
CONNECTION_STATUS_DISCONNECTED = "disconnected"


class ListenerSource(Protocol):
    name: str

    def listen(self) -> AsyncIterator[dict[str, Any]]: ...


class BroadcastSubscription:
    """单个前端 WS 连接的广播订阅。

    每个连接都有自己的队列，避免多个浏览器标签页互相抢走实时消息。
    """

    def __init__(self, hub: "BroadcastHub", queue: asyncio.Queue[dict[str, Any]]):
        self._hub = hub
        self._queue = queue
        self._closed = False

    async def get(self) -> dict[str, Any]:
        return await self._queue.get()

    async def drain(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        while True:
            try:
                messages.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                return messages

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._hub._unsubscribe(self._queue)


class BroadcastHub:
    def __init__(self):
        self._messages: list[dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> BroadcastSubscription:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return BroadcastSubscription(self, queue)

    async def publish(self, message: dict[str, Any]) -> None:
        self._messages.append(message)
        for queue in tuple(self._subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(message)

    async def drain(self) -> list[dict[str, Any]]:
        messages = list(self._messages)
        self._messages.clear()
        return messages

    def _unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)


class Listener:
    def __init__(
        self,
        store: Any | None = None,
        broadcaster: BroadcastHub | None = None,
        trader_manager: Any | None = None,
        discriminator: MatchDiscriminator | None = None,
        sources: list[ListenerSource] | None = None,
        reconnect_delay_seconds: float = 1.0,
    ):
        self._store = store
        self._broadcaster = broadcaster or BroadcastHub()
        self._trader_manager = trader_manager
        self._discriminator = discriminator
        self._sources = sources or []
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []
        self._states: dict[str, dict[str, Any]] = {
            source.name: {
                "connected": False,
                "last_connected_at": None,
                "last_event_at": None,
                "last_error": None,
                "reconnects": 0,
            }
            for source in self._sources
        }
        if self._discriminator is None and self._store is not None:
            self._discriminator = MatchDiscriminator(
                store=self._store,
                broadcaster=self._broadcaster,
                trader_manager=self._trader_manager,
            )

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [asyncio.create_task(self._run_source(source)) for source in self._sources]

    async def stop(self) -> None:
        self._running = False
        tasks = list(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        for state in self._states.values():
            state["connected"] = False

    def status(self) -> dict[str, Any]:
        pm_states = [state for name, state in self._states.items() if name in PM_SOURCE_NAMES]
        gs_states = [state for name, state in self._states.items() if name in GS_SOURCE_NAMES]
        asa_states = [state for name, state in self._states.items() if name in ASA_SOURCE_NAMES]
        pm_connected = bool(pm_states) and any(state["connected"] for state in pm_states)
        gs_connected = bool(gs_states) and any(state["connected"] for state in gs_states)
        asa_connected = bool(asa_states) and any(state["connected"] for state in asa_states)
        pm_last = _latest_state(pm_states)
        gs_last = _latest_state(gs_states)
        asa_last = _latest_state(asa_states)
        pm_market = self._states.get("pm_market")
        pm_user = self._states.get("pm_user")
        pm_sports = self._states.get("pm_sports")
        gs_live = self._states.get("gs_live")
        asa_live = self._states.get("asa_live")
        return {
            "external_stream_enabled": bool(self._sources),
            "external_stream_started": self._running and bool(self._sources),
            "polymarket_ws_enabled": any(name in PM_SOURCE_NAMES for name in self._states),
            "goalserve_ws_enabled": any(name in GS_SOURCE_NAMES for name in self._states),
            "allsports_ws_enabled": any(name in ASA_SOURCE_NAMES for name in self._states),
            "polymarket_ws_connected": pm_connected,
            "pm_market_ws_enabled": pm_market is not None,
            "pm_market_ws_connected": bool(pm_market and pm_market["connected"]),
            "pm_user_ws_enabled": pm_user is not None,
            "pm_user_ws_connected": bool(pm_user and pm_user["connected"]),
            "pm_sports_ws_enabled": pm_sports is not None,
            "pm_sports_ws_connected": bool(pm_sports and pm_sports["connected"]),
            "gs_ws_enabled": gs_live is not None,
            "gs_ws_connected": bool(gs_live and gs_live["connected"]),
            "asa_ws_enabled": asa_live is not None,
            "asa_ws_connected": bool(asa_live and asa_live["connected"]),
            "allsports_ws_connected": asa_connected,
            "polymarket_last_connected_at": pm_last.get("last_connected_at") if pm_last else None,
            "polymarket_last_event_at": pm_last.get("last_event_at") if pm_last else None,
            "polymarket_last_error": pm_last.get("last_error") if pm_last else None,
            "goalserve_connected": gs_connected,
            "goalserve_transport": "ws" if gs_states else None,
            "goalserve_last_connected_at": gs_last.get("last_connected_at") if gs_last else None,
            "goalserve_last_event_at": gs_last.get("last_event_at") if gs_last else None,
            "goalserve_last_error": gs_last.get("last_error") if gs_last else None,
            "allsports_last_connected_at": asa_last.get("last_connected_at") if asa_last else None,
            "allsports_last_event_at": asa_last.get("last_event_at") if asa_last else None,
            "allsports_last_error": asa_last.get("last_error") if asa_last else None,
        }

    async def process_payload(self, source: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self._store is None:
            raise RuntimeError("Listener requires a store")

        # Listener 只处理已能解析 guid 的实时消息；未知比赛进入 dead-letter。
        if source == "pm_market":
            return await self._process_pm_market(payload)
        if source == "pm_sports":
            return await self._process_pm_sports(payload)
        if source == "pm_user":
            return await self._process_pm_user(payload)
        if source == "gs_live":
            return await self._process_gs_live(payload)
        if source == "asa_live":
            return await self._process_asa_live(payload)
        await self._dead_letter(source, payload, "unsupported_source")
        return None

    async def _process_pm_market(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        index = await self._store.get_text(f"idx:pm:asset:{payload.get('asset_id')}")
        if not index:
            await self._dead_letter("pm_market", payload, "unknown_guid")
            return None
        guid, outcome = index.split("|", 1)
        previous = await self._store.get_json(f"pm:match:{guid}") or {}
        if not _is_live_status(previous.get("status")):
            return None
        next_pm = dict(previous)
        bid = _optional_float(payload.get("bid"))
        ask = _optional_float(payload.get("ask"))
        if bid is not None:
            next_pm[f"{outcome}_bid1"] = bid
        if ask is not None:
            next_pm[f"{outcome}_ask1"] = ask
        next_pm["updated_at_utc"] = normalize_ts(payload.get("ts"))
        await self._store.set_json(f"pm:match:{guid}", next_pm, ttl_seconds=CURRENT_STATE_TTL_SECONDS)
        await append_pm_tick_snapshot(self._store, guid, next_pm, next_pm["updated_at_utc"])
        await self._store.set_json(
            f"orderbook:{guid}:{outcome}",
            {
                "guid": guid,
                "outcome_key": outcome,
                "asset_id": payload.get("asset_id"),
                "ask1": ask,
                "bid1": bid,
                "updated_at_utc": next_pm["updated_at_utc"],
                "source": "pm_market",
            },
            ttl_seconds=10 * 60,
        )
        tick_message = {
            "topic": "market.tick",
            "payload": {
                "match_id": guid,
                "outcome": outcome,
                "bid": bid,
                "ask": ask,
                "ts_utc": next_pm["updated_at_utc"],
                "status": next_pm.get("status"),
                "match_time": next_pm.get("match_time"),
                "score_home": next_pm.get("score_home"),
                "score_away": next_pm.get("score_away"),
            },
        }
        await self._broadcaster.publish(tick_message)
        event = {
            "source": "pm_market",
            "event_type": "market_tick",
            "guid": guid,
            "outcome_key": outcome,
            "asset_id": payload.get("asset_id"),
            "ask1": ask,
            "bid1": bid,
            "ts_utc": next_pm["updated_at_utc"],
            "received_at_utc": _utc_now(),
            "status": next_pm.get("status"),
            "match_time": next_pm.get("match_time"),
            "score_home": next_pm.get("score_home"),
            "score_away": next_pm.get("score_away"),
        }
        if self._trader_manager is not None and hasattr(self._trader_manager, "on_market_tick"):
            await self._trader_manager.on_market_tick(event)
        return event

    async def _process_asa_live(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        payload = normalize_allsportsapi_ws_payload(payload)
        guid = None
        if payload.get("inplay_id"):
            guid = await self._store.get_text(f"idx:asa:inplay:{payload.get('inplay_id')}")
        if guid is None and payload.get("match_id"):
            guid = await self._store.get_text(f"idx:asa:id:{payload.get('match_id')}")
        if not guid:
            await self._dead_letter("asa_live", payload, "unknown_guid")
            return None
        pm = await self._store.get_json(f"pm:match:{guid}") or {}
        if _finished_for_more_than(pm, payload.get("ts"), minutes=15):
            return None
        previous = await self._store.get_json(f"asa:match:{guid}") or {}
        next_asa = dict(previous)
        score = payload.get("score") or {}
        if score.get("home") is not None:
            next_asa["score_home"] = score["home"]
        if score.get("away") is not None:
            next_asa["score_away"] = score["away"]
        for key in (
            "status",
            "match_time",
            "period",
            "clock",
            "red_cards",
            "yellow_cards",
            "substitutions",
            "var_events",
            "penalties",
            "free_kicks",
            "corners",
            "shots_on_target",
            "events",
            "lineups",
            "home_logo_url",
            "away_logo_url",
        ):
            if key in payload and _has_meaningful_value(payload[key]):
                next_asa[key] = payload[key]
        next_asa["source"] = "asa"
        next_asa["guid"] = guid
        next_asa["updated_at_utc"] = normalize_ts(payload.get("ts"))
        await self._store.set_json(f"asa:match:{guid}", next_asa, ttl_seconds=CURRENT_STATE_TTL_SECONDS)
        await self._store.set_json(f"external:match:{guid}", next_asa, ttl_seconds=CURRENT_STATE_TTL_SECONDS)
        await self._publish_external_match(guid, next_asa)
        await self._mark_pm_finished_from_external(guid, next_asa, source="asa_live")
        event = await self._discriminator.process_external_state(
            source="asa_live",
            guid=guid,
            payload=_with_pm_score_snapshot(payload, pm),
            previous=previous,
            current=next_asa,
            mapping={
                "score_home": "score_home",
                "score_away": "score_away",
                "red_cards": "red_cards",
                "yellow_cards": "yellow_cards",
                "penalties": "penalties",
                "free_kicks": "free_kicks",
                "corners": "corners",
                "shots_on_target": "shots_on_target",
            },
            ws_message=_trader_event(guid, pm, _source_event_clock(next_asa)),
        )
        return event

    async def _process_pm_sports(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        guid = await self._resolve_pm_sports_guid(payload)
        if not guid:
            await self._dead_letter("pm_sports", payload, "unknown_guid")
            return None
        previous = await self._store.get_json(f"pm:match:{guid}") or {}
        next_pm = dict(previous)
        score = payload.get("score") or {}
        if _is_score_regression(previous, score):
            await self._dead_letter("pm_sports", payload, "score_regression")
            score = {}
        if "home" in score:
            next_pm["score_home"] = score["home"]
        if "away" in score:
            next_pm["score_away"] = score["away"]
        if payload.get("status"):
            next_pm["status"] = payload["status"]
        if payload.get("match_time"):
            next_pm["match_time"] = payload["match_time"]
        next_pm["updated_at_utc"] = normalize_ts(payload.get("ts"))
        await self._store.set_json(f"pm:match:{guid}", next_pm, ttl_seconds=CURRENT_STATE_TTL_SECONDS)
        event = await self._discriminator.process_external_state(
            source="pm_sports",
            guid=guid,
            payload=payload,
            previous=previous,
            current=next_pm,
            mapping={
                "score_home": "score_home",
                "score_away": "score_away",
                "status": "status",
                "match_time": "match_time",
            },
            ws_message=_trader_event(guid, next_pm, _source_event_clock(next_pm)),
        )
        return event

    async def _process_gs_live(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        payload = _normalize_gs_live_payload(payload)
        guid = None
        if payload.get("inplay_id"):
            guid = await self._store.get_text(f"idx:gs:inplay:{payload.get('inplay_id')}")
        if guid is None and payload.get("match_id"):
            guid = await self._store.get_text(f"idx:gs:id:{payload.get('match_id')}")
        if not guid:
            await self._dead_letter("gs_live", payload, "unknown_guid")
            return None
        pm = await self._store.get_json(f"pm:match:{guid}") or {}
        if _finished_for_more_than(pm, payload.get("ts"), minutes=15):
            return None
        previous = await self._store.get_json(f"gs:match:{guid}") or {}
        next_gs = dict(previous)
        score = payload.get("score") or {}
        if score.get("home") is not None:
            next_gs["score_home"] = score["home"]
        if score.get("away") is not None:
            next_gs["score_away"] = score["away"]
        for key in (
            "status",
            "match_time",
            "period",
            "clock",
            "red_cards",
            "yellow_cards",
            "substitutions",
            "var_events",
            "penalties",
            "free_kicks",
            "corners",
            "shots_on_target",
            "events",
            "lineups",
        ):
            if key in payload and _has_meaningful_value(payload[key]):
                next_gs[key] = payload[key]
        next_gs["updated_at_utc"] = normalize_ts(payload.get("ts"))
        next_gs["source"] = "gs"
        next_gs["guid"] = guid
        await self._store.set_json(f"gs:match:{guid}", next_gs, ttl_seconds=CURRENT_STATE_TTL_SECONDS)
        await self._store.set_json(f"external:match:{guid}", next_gs, ttl_seconds=CURRENT_STATE_TTL_SECONDS)
        await self._publish_external_match(guid, next_gs)
        await self._mark_pm_finished_from_external(guid, next_gs, source="gs_live")
        event = await self._discriminator.process_external_state(
            source="gs_live",
            guid=guid,
            payload=_with_pm_score_snapshot(payload, pm),
            previous=previous,
            current=next_gs,
            mapping={
                "score_home": "score_home",
                "score_away": "score_away",
                "red_cards": "red_cards",
                "yellow_cards": "yellow_cards",
                "penalties": "penalties",
                "free_kicks": "free_kicks",
                "corners": "corners",
                "shots_on_target": "shots_on_target",
            },
            ws_message=_trader_event(guid, pm, _source_event_clock(next_gs)),
        )
        return event

    async def _publish_external_match(self, guid: str, state: dict[str, Any]) -> None:
        # 外部源每次标准化后都推送当前状态；是否发生赛况变化交给判别器内存比较。
        if self._broadcaster is not None:
            await self._broadcaster.publish({"topic": "external.match", "payload": {"guid": guid, **state}})

    async def _mark_pm_finished_from_external(self, guid: str, state: dict[str, Any], *, source: str) -> None:
        if not _is_finished_status(state.get("status")):
            return
        pm = await self._store.get_json(f"pm:match:{guid}") or {}
        if not pm or _is_finished_status(pm.get("status")):
            return
        finished_at = normalize_ts(state.get("updated_at_utc") or state.get("ts") or _utc_now())
        next_pm = {
            **pm,
            "status": "finished",
            "finished_at_utc": finished_at,
            "updated_at_utc": finished_at,
            "status_source": source,
        }
        await self._store.set_json(f"pm:match:{guid}", next_pm, ttl_seconds=CURRENT_STATE_TTL_SECONDS)
        if self._broadcaster is not None:
            await self._broadcaster.publish({"topic": "match.snapshot", "payload": _pm_match_snapshot(guid, next_pm)})

    async def _process_pm_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        alias = payload.get("account_alias") or "default"
        provider = str(payload.get("provider") or "pm").lower()
        now = payload.get("ts") or _utc_now()
        changed_fields: list[str] = []
        account: dict[str, Any] | None = None
        if "balance" in payload or "available_cash" in payload:
            account = {
                "provider": provider,
                "account_alias": alias,
                "balance": payload.get("balance"),
                "available_cash": payload.get("available_cash"),
                "updated_at_utc": now,
            }
            await self._store.set_json(f"account:{alias}", account)
            await self._store.set_json(f"account:{provider}:{alias}", account)
            changed_fields.append("account")
            await self._broadcaster.publish({"topic": "account.update", "payload": account})
        orders: list[dict[str, Any]] = []
        for order in payload.get("orders") or []:
            row = dict(order, provider=provider, account_alias=alias, ts_utc=now, guid=await self._guid_from_order(order))
            orders.append(row)
            await self._store.add_stream("stream:orders", row, ttl_seconds=MATCH_RELATED_TTL_SECONDS)
            changed_fields.append("orders")
        fills: list[dict[str, Any]] = []
        for fill in payload.get("fills") or []:
            row = dict(fill, provider=provider, account_alias=alias, ts_utc=now)
            fills.append(row)
            await self._store.add_stream("stream:fills", row, ttl_seconds=MATCH_RELATED_TTL_SECONDS)
            changed_fields.append("fills")
        event = {
            "received_at_utc": _utc_now(),
            "pushed_at_utc": _utc_now(),
            "source": "pm_user",
            "provider": provider,
            "account_alias": alias,
            "guid": None,
            "changed_fields": list(dict.fromkeys(changed_fields)),
            "account": account,
            "orders": orders,
            "fills": fills,
            "raw_ref": payload.get("message_id"),
        }
        await self._store.add_stream(f"stream:account_events:{provider}:{alias}", event)
        if self._trader_manager is not None and hasattr(self._trader_manager, "on_account_event"):
            await self._trader_manager.on_account_event(event)
        return event

    async def _dead_letter(self, source: str, payload: dict[str, Any], reason: str) -> None:
        await self._store.add_stream(
            "stream:dead_letters",
            {"source": source, "reason": reason, "payload": payload, "ts_utc": _utc_now()},
            ttl_seconds=MATCH_RELATED_TTL_SECONDS,
        )

    async def _resolve_pm_sports_guid(self, payload: dict[str, Any]) -> str | None:
        for key, prefix in (
            ("event_id", "idx:pm:event"),
            ("slug", "idx:pm:slug"),
            ("game_id", "idx:pm:game"),
        ):
            value = payload.get(key)
            if value:
                guid = await self._store.get_text(f"{prefix}:{value}")
                if guid:
                    return guid
        return None

    async def _guid_from_order(self, order: dict[str, Any]) -> str | None:
        asset_id = order.get("asset_id")
        if not asset_id:
            return None
        index = await self._store.get_text(f"idx:pm:asset:{asset_id}")
        if not index:
            return None
        return index.split("|", 1)[0]

    async def _run_source(self, source: ListenerSource) -> None:
        state = self._states[source.name]
        while self._running:
            try:
                state["connected"] = False
                async for payload in source.listen():
                    if not self._running:
                        break
                    if payload.get(CONNECTION_STATUS_FIELD) == CONNECTION_STATUS_CONNECTED:
                        state["connected"] = True
                        state["last_connected_at"] = _utc_now()
                        state["last_error"] = None
                        continue
                    if payload.get(CONNECTION_STATUS_FIELD) == CONNECTION_STATUS_DISCONNECTED:
                        state["connected"] = False
                        state["last_event_at"] = _utc_now()
                        continue
                    if not state["connected"]:
                        state["connected"] = True
                        state["last_connected_at"] = _utc_now()
                        state["last_error"] = None
                    await self.process_payload(source.name, payload)
                    state["last_event_at"] = _utc_now()
                if self._running:
                    state["connected"] = False
                    state["reconnects"] += 1
                    await asyncio.sleep(self._reconnect_delay_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state["connected"] = False
                state["last_error"] = exc.__class__.__name__
                state["reconnects"] += 1
                await asyncio.sleep(self._reconnect_delay_seconds)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_gs_live_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("score") or payload.get("inplay_id") or payload.get("match_id"):
        return payload
    return normalize_goalserve_ws_payload(payload)


def _latest_state(states: list[dict[str, Any]]) -> dict[str, Any]:
    if not states:
        return {}
    return max(
        states,
        key=lambda state: (
            state.get("last_event_at") or "",
            state.get("last_connected_at") or "",
            state.get("last_error") or "",
        ),
    )


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _has_meaningful_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple, set)) and not value:
        return False
    if isinstance(value, dict) and not value:
        return False
    return True


def _is_live_status(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "live",
        "inprogress",
        "in_progress",
        "in-play",
        "inplay",
        "break",
        "halftime",
        "half_time",
        "ht",
    }


def _with_pm_score_snapshot(payload: dict[str, Any], pm: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "pm_score_home_at_event": pm.get("score_home"),
        "pm_score_away_at_event": pm.get("score_away"),
    }


def _is_finished_status(value: Any) -> bool:
    return str(value or "").strip().lower() in {"finished", "final", "ended", "closed", "complete", "completed"}


def _pm_match_snapshot(guid: str, pm: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_id": guid,
        "sport": "football",
        "league": pm.get("league") or "",
        "team_home": pm.get("home_team") or "",
        "team_away": pm.get("away_team") or "",
        "home_logo_url": pm.get("home_logo_url"),
        "away_logo_url": pm.get("away_logo_url"),
        "start_time_utc": pm.get("start_time_utc") or "",
        "status": pm.get("status") or "",
        "moneyline_volume": pm.get("moneyline_volume") or 0,
        "total_volume": pm.get("total_volume") or 0,
        "latest_ts_utc": pm.get("updated_at_utc") or _utc_now(),
        "score_home": pm.get("score_home"),
        "score_away": pm.get("score_away"),
        "pm_match_time": pm.get("match_time"),
        "external_event_id": pm.get("pm_event_id"),
        "external_event_slug": pm.get("slug"),
        "external_market_id": pm.get("condition_id"),
        "external_market_slug": pm.get("slug"),
        "home_bid": pm.get("home_bid1"),
        "home_ask": pm.get("home_ask1"),
        "away_bid": pm.get("away_bid1"),
        "away_ask": pm.get("away_ask1"),
        "draw_bid": pm.get("draw_bid1"),
        "draw_ask": pm.get("draw_ask1"),
    }


def _finished_for_more_than(pm: dict[str, Any], ts_utc: Any, *, minutes: int) -> bool:
    if not _is_finished_status(pm.get("status")):
        return False
    finished_at = _parse_datetime(str(pm.get("finished_at_utc") or pm.get("updated_at_utc") or ""))
    if finished_at is None:
        return False
    current = _parse_datetime(str(ts_utc or "")) or datetime.now(UTC)
    return current - finished_at > timedelta(minutes=minutes)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_score_regression(previous: dict[str, Any], score: dict[str, Any]) -> bool:
    """PM sports 偶发旧比分回放时，不允许足球比分倒退覆盖当前态。"""

    if not isinstance(score, dict):
        return False
    for side, field in (("home", "score_home"), ("away", "score_away")):
        if side not in score or previous.get(field) is None or score.get(side) is None:
            continue
        try:
            if int(score[side]) < int(previous[field]):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _trader_event(guid: str, pm: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic": "trader.event",
        "payload": {
            "event_id": guid,
            "local_ts_utc": event["pushed_at_utc"],
            "source_ts_utc": event["received_at_utc"],
            "pm": {
                "status": pm.get("status"),
                "match_time": pm.get("match_time"),
                "home_score": pm.get("score_home"),
                "away_score": pm.get("score_away"),
                "markets": [
                    {
                        "market_type": "moneyline",
                        "outcome_key": outcome,
                        "bid1": pm.get(f"{outcome}_bid1"),
                        "ask1": pm.get(f"{outcome}_ask1"),
                    }
                    for outcome in ("home", "draw", "away")
                ],
            },
        },
    }


def _source_event_clock(state: dict[str, Any]) -> dict[str, str]:
    ts = state.get("updated_at_utc") or _utc_now()
    return {"pushed_at_utc": ts, "received_at_utc": ts}
