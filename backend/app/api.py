"""HTTP/WS API 层：只做展示聚合和命令入口，不直接实现采集或交易策略。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import asyncio
import re
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .models import (
    AuthLoginPayload,
    AuthSessionResponse,
    CollectorSettings,
    CollectorStatus,
    ManualExternalBindPayload,
    SimulationStartPayload,
    TradingCreatePayload,
    TradingSnapshot,
    TradingUpdatePayload,
)
from .pm_accounts import has_pm_account, public_pm_accounts_with_balances, public_pm_positions, public_pm_trades
from .retention import cleanup_retention
from .timeseries import get_series_rows

router = APIRouter(prefix="/api/v1")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _trader(request: Request):
    return request.app.state.trader


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    # 健康检查只暴露连通状态，避免把 Redis URL 等内部配置返回给前端。
    redis_connected = await request.app.state.store.ping()
    return {
        "status": "ok",
        "runtime": request.app.state.runtime.status(),
        "redis": {
            "connected": redis_connected,
        },
    }


@router.get("/auth/session", response_model=AuthSessionResponse)
async def auth_session(request: Request) -> AuthSessionResponse:
    auth = request.app.state.auth_manager
    session_id = request.cookies.get(request.app.state.settings.auth_cookie_name)
    authenticated = await auth.validate_session(session_id)
    return AuthSessionResponse(**auth.session_payload(authenticated))


@router.post("/auth/login", response_model=AuthSessionResponse)
async def auth_login(request: Request, payload: AuthLoginPayload) -> JSONResponse:
    auth = request.app.state.auth_manager
    session = await auth.login(payload.password)
    if session is None:
        raise HTTPException(status_code=401, detail="invalid_password")
    body = auth.session_payload(True)
    body["expires_at_utc"] = session["expires_at_utc"]
    response = JSONResponse(body)
    response.set_cookie(
        request.app.state.settings.auth_cookie_name,
        session["session_id"],
        max_age=auth.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return response


@router.post("/auth/logout", response_model=AuthSessionResponse)
async def auth_logout(request: Request) -> JSONResponse:
    auth = request.app.state.auth_manager
    session_id = request.cookies.get(request.app.state.settings.auth_cookie_name)
    await auth.logout(session_id)
    response = JSONResponse(auth.session_payload(False))
    response.delete_cookie(request.app.state.settings.auth_cookie_name, path="/")
    return response


@router.get("/connectivity")
async def connectivity(request: Request) -> dict[str, Any]:
    return await request.app.state.connectivity_checker.check_all()


@router.post("/retention/cleanup")
async def retention_cleanup(request: Request) -> dict[str, Any]:
    deleted = await cleanup_retention(request.app.state.store)
    return {"deleted": deleted}


@router.get("/strategies/catalog")
async def strategies_catalog() -> list[dict[str, Any]]:
    return [
        {
            "key": "football_score_delay_trade",
            "display_name": "足球-比分时差交易",
            "supported_sports": ["football"],
            "params": [],
        },
        {
            "key": "football_winrate_gap_buy",
            "display_name": "胜率差买入",
            "supported_sports": ["football"],
            "params": [
                {
                    "key": "winrate_gap_pct",
                    "display_name": "胜率差阈值",
                    "value_type": "number",
                    "required": True,
                    "unit": "%",
                    "min": 1,
                    "max": 100,
                    "decimals": 0,
                    "default": 30,
                    "description": "主队与客队 ask1 概率差达到该百分比才买入高胜率队。",
                },
                {
                    "key": "entry_before_minutes",
                    "display_name": "开赛前窗口",
                    "value_type": "number",
                    "required": True,
                    "unit": "分钟",
                    "min": 0,
                    "max": 60,
                    "decimals": 0,
                    "default": 5,
                    "description": "开赛前多少分钟内允许触发入场。",
                },
                {
                    "key": "entry_after_minutes",
                    "display_name": "开赛后窗口",
                    "value_type": "number",
                    "required": True,
                    "unit": "分钟",
                    "min": 0,
                    "max": 90,
                    "decimals": 0,
                    "default": 15,
                    "description": "0-0 时开赛后多少分钟内允许触发入场。",
                },
            ],
        },
    ]


@router.get("/settings/collector", response_model=CollectorSettings)
async def get_collector_settings(request: Request) -> CollectorSettings:
    return request.app.state.collector_settings


@router.put("/settings/collector", response_model=CollectorSettings)
async def put_collector_settings(
    request: Request,
    payload: CollectorSettings,
) -> CollectorSettings:
    request.app.state.collector_settings = payload
    await request.app.state.store.set_json("settings:collector", payload.model_dump())
    request.app.state.collector.set_interval_minutes(payload.collection_interval_minutes)
    request.app.state.collector.set_filters(
        football_volume_threshold_k=payload.football_volume_threshold_k,
        upcoming_days=2,
    )
    if hasattr(request.app.state.collector, "set_external_source"):
        request.app.state.collector.set_external_source(payload.external_source)
    await request.app.state.collector.collect_once()
    return payload


@router.get("/collector/status", response_model=CollectorStatus)
async def collector_status(request: Request) -> CollectorStatus:
    status = request.app.state.collector.status().model_dump()
    status.update(request.app.state.listener.status())
    return CollectorStatus(**status)


@router.post("/collector/run")
async def collector_run(request: Request) -> dict[str, Any]:
    return await request.app.state.collector.collect_once()


@router.get("/matches")
async def matches(request: Request) -> list[dict[str, Any]]:
    return await _match_cards(
        request.app.state.store,
        finished=False,
        collector_settings=(
            request.app.state.collector_settings
            if getattr(request.app.state, "enforce_collector_display_filter", True)
            else None
        ),
        timezone_name=request.app.state.settings.auth_timezone,
    )


@router.get("/matches/history")
async def matches_history(request: Request, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    rows = await _match_cards(request.app.state.store, finished=True)
    return rows[offset : offset + limit]


@router.get("/ticks")
async def ticks(request: Request, match_id: str, limit: int = 1000) -> list[Any]:
    return await get_series_rows(request.app.state.store, f"series:pm:ticks:{match_id}", limit=limit)


@router.get("/matches/{match_id}/snapshots")
async def match_snapshots(
    request: Request,
    match_id: str,
    limit: int = 1000,
    series: str = "both",
) -> list[Any]:
    requested = series.strip().lower()
    keys: list[str]
    if requested == "live":
        keys = [f"series:pm:10s:{match_id}"]
    elif requested in {"all", "pre"}:
        keys = [f"series:pm:collector:{match_id}"]
    else:
        keys = [f"series:pm:collector:{match_id}", f"series:pm:10s:{match_id}"]
    pm = await request.app.state.store.get_json(f"pm:match:{match_id}") or {}
    rows: list[dict[str, Any]] = []
    for key in keys:
        value = await get_series_rows(request.app.state.store, key)
        if isinstance(value, list):
            rows.extend(_enrich_snapshot_rows(match_id, pm, value))
    rows.sort(key=lambda row: row.get("snapshot_ts_utc") or "")
    return rows[-limit:]


@router.get("/accounts")
async def accounts(request: Request) -> list[dict[str, Any]]:
    timezone_name = request.app.state.settings.auth_timezone
    pm_accounts = await _pm_accounts_by_alias(request)
    memory_rows = _accounts_from_trader(_trader(request), timezone_name, pm_accounts)
    if memory_rows:
        return memory_rows
    rows = []
    rows.extend(await _accounts_from_store(request.app.state.store, timezone_name, pm_accounts))
    return rows


@router.get("/pm/accounts")
async def pm_accounts(request: Request) -> list[dict[str, Any]]:
    try:
        return await public_pm_accounts_with_balances(request.app.state.settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/positions")
async def positions(request: Request) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    snapshots = _trader(request).list_tradings()
    for snapshot in snapshots:
        if snapshot.mode == "real":
            account = _trader(request).get_account(snapshot.trading_id)
            for position in await public_pm_positions(request.app.state.settings, account.account_alias):
                rows.append({"source": "pm", "trading_id": snapshot.trading_id, **position})
            continue
        for position in _trader(request).get_positions(snapshot.trading_id):
            rows.append({"trading_id": snapshot.trading_id, **asdict(position)})
    if snapshots:
        return await _enrich_trading_rows(request.app.state.store, rows)
    if not rows:
        store_rows = await _list_json_by_pattern(request.app.state.store, "trader:*:positions")
        rows = [row for group in store_rows for row in group]
    return await _enrich_trading_rows(request.app.state.store, rows)


@router.get("/trades")
async def trades(
    request: Request,
    trading_id: str | None = None,
    match_id: str | None = None,
    limit: int = 200,
) -> list[Any]:
    rows: list[dict[str, Any]] = []
    snapshots = _trader(request).list_tradings()
    match_pm = await request.app.state.store.get_json(f"pm:match:{match_id}") if match_id else None
    for snapshot in snapshots:
        if trading_id and snapshot.trading_id != trading_id:
            continue
        if snapshot.mode == "real":
            account = _trader(request).get_account(snapshot.trading_id)
            for trade in await public_pm_trades(request.app.state.settings, account.account_alias, limit=limit):
                if match_id and not _pm_row_matches_match(trade, match_id, match_pm):
                    continue
                rows.append({"source": "pm", "trading_id": snapshot.trading_id, **trade})
            continue
        for trade in _trader(request).get_trades(snapshot.trading_id):
            if match_id and trade.get("guid") != match_id:
                continue
            rows.append({"trading_id": snapshot.trading_id, **trade})
    if snapshots:
        rows = await _enrich_trading_rows(request.app.state.store, rows)
        return sorted(rows, key=lambda row: str(row.get("ts_utc") or ""), reverse=True)[:limit]
    if not rows:
        store_rows = await _trader_rows_from_store(request.app.state.store, "trades")
        rows = [row for group in store_rows for row in group]
        if trading_id:
            rows = [
                row
                for row in rows
                if row.get("trading_id") == trading_id or row.get("trader_id") == trading_id
            ]
        if match_id:
            rows = [row for row in rows if row.get("guid") == match_id]
    rows = await _enrich_trading_rows(request.app.state.store, rows)
    return sorted(rows, key=lambda row: str(row.get("ts_utc") or ""), reverse=True)[:limit]


@router.get("/logs")
async def logs(
    request: Request,
    trading_id: str | None = None,
    match_id: str | None = None,
    limit: int = 200,
) -> list[Any]:
    rows: list[dict[str, Any]] = []

    # 比赛过程日志由判别器写入独立 stream；它不属于某个交易员，只有未按交易员筛选时返回。
    if not trading_id:
        for row in await request.app.state.store.stream("stream:match_logs", limit=limit):
            if match_id and row.get("guid") != match_id:
                continue
            rows.append(row)

    store_rows = await _trader_rows_from_store(request.app.state.store, "logs")
    if store_rows:
        trader_rows = [row for group in store_rows for row in group]
        if trading_id:
            trader_rows = [
                row
                for row in trader_rows
                if row.get("trader_id") == trading_id or row.get("trading_id") == trading_id
            ]
        if match_id:
            trader_rows = [row for row in trader_rows if row.get("guid") == match_id]
        rows.extend(trader_rows)
        return sorted(rows, key=lambda row: str(row.get("ts_utc") or ""))[-limit:]
    for snapshot in _trader(request).list_tradings():
        if trading_id and snapshot.trading_id != trading_id:
            continue
        for log in _trader(request).get_logs(snapshot.trading_id):
            if match_id and log.get("guid") != match_id:
                continue
            rows.append(log)
    return sorted(rows, key=lambda row: str(row.get("ts_utc") or ""))[-limit:]


@router.get("/external-source/match/{match_id}")
async def external_source_match(request: Request, match_id: str) -> dict[str, Any]:
    source = str(request.app.state.collector_settings.external_source)
    if source == "asa":
        row = await request.app.state.store.get_json(f"asa:match:{match_id}") or {}
        return _with_external_widget(row, source="asa", settings=request.app.state.settings, guid=match_id)
    if source == "gs":
        row = await request.app.state.store.get_json(f"gs:match:{match_id}") or {}
        return _with_external_widget(row, source="gs", settings=request.app.state.settings, guid=match_id)
    row = await request.app.state.store.get_json(f"external:match:{match_id}") or {}
    return _with_external_widget(row, source=str(row.get("source") or ""), settings=request.app.state.settings, guid=match_id)


@router.get("/matches/{match_id}/external-candidates")
async def match_external_candidates(
    request: Request,
    match_id: str,
    source: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    try:
        return await request.app.state.collector.external_candidates_for_match(
            match_id,
            source=source,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/matches/{match_id}/external-bind")
async def match_external_bind(
    request: Request,
    match_id: str,
    payload: ManualExternalBindPayload,
) -> dict[str, Any]:
    try:
        return await request.app.state.collector.bind_external_match(
            match_id,
            source=payload.source,
            external_match_id=payload.external_match_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/goalserve/match/{match_id}")
async def goalserve_match(request: Request, match_id: str) -> dict[str, Any]:
    return await request.app.state.store.get_json(f"gs:match:{match_id}") or {}


@router.get("/allsportsapi/match/{match_id}")
async def allsportsapi_match(request: Request, match_id: str) -> dict[str, Any]:
    return await request.app.state.store.get_json(f"asa:match:{match_id}") or {}


@router.post("/simulation/start")
async def start_simulation(
    request: Request,
    payload: SimulationStartPayload,
) -> dict[str, bool]:
    return {"running": _trader(request).start_simulation()}


@router.post("/simulation/stop")
async def stop_simulation(request: Request) -> dict[str, bool]:
    return {"running": _trader(request).stop_simulation()}


@router.get("/tradings", response_model=list[TradingSnapshot])
async def list_tradings(request: Request) -> list[TradingSnapshot]:
    return _trader(request).list_tradings()


@router.post("/tradings", response_model=TradingSnapshot)
async def create_trading(
    request: Request,
    payload: TradingCreatePayload,
) -> TradingSnapshot:
    try:
        if payload.mode == "real" and not has_pm_account(request.app.state.settings, payload.account_alias):
            raise ValueError("real trading requires a configured PM account_alias")
        return await _trader(request).create_trading(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/tradings/{trading_id}", response_model=TradingSnapshot)
async def update_trading(
    request: Request,
    trading_id: str,
    payload: TradingUpdatePayload,
) -> TradingSnapshot:
    try:
        return await _trader(request).update_trading(trading_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tradings/{trading_id}/start", response_model=TradingSnapshot)
async def start_trading(request: Request, trading_id: str) -> TradingSnapshot:
    try:
        return await _trader(request).start_trading(trading_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tradings/{trading_id}/stop", response_model=TradingSnapshot)
async def stop_trading(request: Request, trading_id: str) -> TradingSnapshot:
    try:
        return await _trader(request).stop_trading(trading_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/tradings/{trading_id}")
async def delete_trading(request: Request, trading_id: str) -> dict[str, bool]:
    return {"deleted": await _trader(request).delete_trading(trading_id)}


@router.post("/soak/dry-run")
async def dry_run_soak(request: Request, iterations: int = 1) -> dict[str, Any]:
    return await request.app.state.soak_runner.run(iterations=iterations)


@router.websocket("/ws/market")
async def market_ws(websocket: WebSocket) -> None:
    # 每个前端 WS 连接使用独立订阅队列，多个浏览器标签页不会互相抢消息。
    if websocket.app.state.settings.auth_enabled:
        session_id = websocket.cookies.get(websocket.app.state.settings.auth_cookie_name)
        if not await websocket.app.state.auth_manager.validate_session(session_id):
            await websocket.close(code=1008)
            return None
    await websocket.accept()
    subscription = websocket.app.state.broadcaster.subscribe()
    interval = websocket.app.state.settings.heartbeat_interval_seconds
    loop = asyncio.get_running_loop()
    next_heartbeat_at = loop.time() + interval
    try:
        await websocket.send_json(
            {
                "type": "heartbeat",
                "source": "pdt2.1",
                "ts_utc": _utc_now(),
            }
        )
        while True:
            now = loop.time()
            timeout = min(1.0, max(0.1, next_heartbeat_at - now))
            try:
                message = await asyncio.wait_for(subscription.get(), timeout=timeout)
            except asyncio.TimeoutError:
                message = None
            if message is not None:
                await websocket.send_json(message)
                for queued_message in await subscription.drain():
                    await websocket.send_json(queued_message)
            now = loop.time()
            if now >= next_heartbeat_at:
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "source": "pdt2.1",
                        "ts_utc": _utc_now(),
                    }
                )
                next_heartbeat_at = now + interval
    except (WebSocketDisconnect, RuntimeError):
        return None
    finally:
        subscription.close()


async def _match_cards(
    store: Any,
    finished: bool,
    collector_settings: CollectorSettings | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    keys = await store.keys("pm:match:*")
    values = await _get_many_json(store, keys)
    binding_values = await _get_many_json(
        store,
        [
            f"binding:{pm['guid']}"
            for pm in values
            if isinstance(pm, dict) and pm.get("guid")
        ],
    )
    bindings_by_guid = {
        binding["guid"]: binding
        for binding in binding_values
        if isinstance(binding, dict) and binding.get("guid")
    }
    external_values = await _get_many_json(
        store,
        [
            f"external:match:{pm['guid']}"
            for pm in values
            if isinstance(pm, dict) and pm.get("guid")
        ],
    )
    external_by_guid = {
        external["guid"]: external
        for external in external_values
        if isinstance(external, dict) and external.get("guid")
    }
    for key, external in zip(
        [
            pm["guid"]
            for pm in values
            if isinstance(pm, dict) and pm.get("guid")
        ],
        external_values,
        strict=False,
    ):
        if isinstance(external, dict):
            external_by_guid[key] = external
    for pm in values:
        if not pm:
            continue
        status = str(pm.get("status") or "")
        is_finished = status.lower() in {"finished", "ended", "closed"}
        if is_finished != finished:
            continue
        if not finished and collector_settings is not None:
            if not _passes_current_match_filter(pm, collector_settings, timezone_name):
                continue
        guid = pm["guid"]
        binding = bindings_by_guid.get(guid, {})
        external = external_by_guid.get(guid, {})
        moneyline_volume = _float_or_zero(pm.get("moneyline_volume"))
        total_volume = _float_or_zero(pm.get("total_volume"))
        rows.append(
            {
                "match_id": guid,
                "sport": "football",
                "league": pm.get("league") or "",
                "team_home": pm.get("home_team") or "",
                "team_away": pm.get("away_team") or "",
                "home_logo_url": pm.get("home_logo_url") or _team_logo_url(pm, "home"),
                "away_logo_url": pm.get("away_logo_url") or _team_logo_url(pm, "away"),
                "start_time_utc": pm.get("start_time_utc") or "",
                "status": pm.get("status") or "",
                "moneyline_volume": moneyline_volume,
                "total_volume": total_volume,
                "latest_ts_utc": _latest_timestamp(
                    pm.get("updated_at_utc"),
                    external.get("updated_at_utc") if isinstance(external, dict) else None,
                )
                or _utc_now(),
                "score_home": pm.get("score_home"),
                "score_away": pm.get("score_away"),
                "pm_match_time": pm.get("match_time"),
                "external_event_id": pm.get("pm_event_id"),
                "external_event_slug": pm.get("slug"),
                "external_market_id": pm.get("condition_id"),
                "external_market_slug": pm.get("slug"),
                "game_id": pm.get("raw", {}).get("game_id") if isinstance(pm.get("raw"), dict) else None,
                "home_bid": pm.get("home_bid1"),
                "home_ask": pm.get("home_ask1"),
                "home_price": _mid(pm.get("home_bid1"), pm.get("home_ask1")),
                "away_bid": pm.get("away_bid1"),
                "away_ask": pm.get("away_ask1"),
                "away_price": _mid(pm.get("away_bid1"), pm.get("away_ask1")),
                "draw_bid": pm.get("draw_bid1"),
                "draw_ask": pm.get("draw_ask1"),
                "draw_price": _mid(pm.get("draw_bid1"), pm.get("draw_ask1")),
                "external_source": binding.get("external_source"),
                "external_match_id": binding.get("external_match_id"),
                "external_bound": bool(
                    binding.get("status") == "matched"
                    and binding.get("external_source")
                    and binding.get("external_match_id")
                ),
                "binding_status": binding.get("status") or "pending",
            }
        )
    return sorted(rows, key=lambda row: row["start_time_utc"])


def _enrich_snapshot_rows(match_id: str, pm: dict[str, Any], rows: list[Any]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        merged = {
            "match_id": match_id,
            "source": row.get("source") or "pm_http",
            "sport": row.get("sport") or pm.get("sport") or "football",
            "status": row.get("status") or pm.get("status") or "",
            "phase": row.get("phase") or "ALL",
            "start_time_utc": row.get("start_time_utc") or pm.get("start_time_utc") or "",
            "team_home": row.get("team_home") or pm.get("home_team") or "",
            "team_away": row.get("team_away") or pm.get("away_team") or "",
            "external_event_id": row.get("external_event_id") or pm.get("pm_event_id"),
            "external_event_slug": row.get("external_event_slug") or pm.get("slug"),
            "external_market_id": row.get("external_market_id") or pm.get("condition_id"),
            "external_market_slug": row.get("external_market_slug") or pm.get("slug"),
            "game_id": row.get("game_id") or (
                (
                    pm.get("raw", {}).get("game_id")
                    or pm.get("raw", {}).get("gameId")
                )
                if isinstance(pm.get("raw"), dict)
                else None
            ),
            "score_home": row.get("score_home", pm.get("score_home")),
            "score_away": row.get("score_away", pm.get("score_away")),
            "moneyline_volume": row.get("moneyline_volume", pm.get("moneyline_volume")),
            "total_volume": row.get("total_volume", pm.get("total_volume")),
            **row,
        }
        enriched.append(merged)
    return enriched


def _mid(bid: Any, ask: Any) -> float | None:
    if bid is None or ask is None:
        return None
    return round((float(bid) + float(ask)) / 2, 6)


def _team_logo_url(pm: dict[str, Any], side: str) -> str | None:
    raw = pm.get("raw") if isinstance(pm.get("raw"), dict) else {}
    target = _normalize_team(pm.get(f"{side}_team") or "")
    candidates = [raw.get("teams")]
    if isinstance(raw.get("raw"), dict):
        candidates.append(raw["raw"].get("teams"))
    for teams in candidates:
        for team in teams or []:
            if not isinstance(team, dict):
                continue
            name = _normalize_team(str(team.get("name") or team.get("title") or team.get("displayName") or ""))
            logo = team.get("logo") or team.get("image") or team.get("icon") or team.get("logoUrl")
            if target and name == target and logo:
                return str(logo)
    return None


def _normalize_team(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _with_external_widget(
    row: dict[str, Any],
    *,
    source: str,
    settings: Any,
    guid: str,
) -> dict[str, Any]:
    if not row:
        return {}
    enriched = dict(row)
    normalized_source = str(source or row.get("source") or "").strip().lower()
    template = None
    provider_url = None
    if normalized_source == "asa":
        template = settings.allsports_widget_url_template
        provider_url = "https://allsportsapi.com/widgets/football-soccer/livescore/download"
    elif normalized_source == "gs":
        template = settings.goalserve_widget_url_template
        provider_url = "https://www.goalserve.com/en/sport-data-widgets/match-details-widget/details"
    widget_url = _format_widget_url(template, row, guid) if template else provider_url
    enriched["widget_url"] = widget_url
    enriched["widget_provider_url"] = provider_url
    enriched["widget_source"] = normalized_source or None
    return enriched


def _format_widget_url(template: str | None, row: dict[str, Any], guid: str) -> str | None:
    if not template:
        return None
    values = {
        "guid": guid,
        "match_id": row.get("match_id") or row.get("external_match_id") or "",
        "event_key": row.get("match_id") or row.get("event_key") or row.get("external_match_id") or "",
        "inplay_id": row.get("inplay_id") or row.get("match_id") or "",
        "pregame_id": row.get("pregame_id") or row.get("match_id") or "",
        "home_team": row.get("home_team") or "",
        "away_team": row.get("away_team") or "",
    }
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", quote(str(value), safe=""))
    return result


async def _list_json_by_pattern(store: Any, pattern: str) -> list[Any]:
    keys = await store.keys(pattern)
    return [value for value in await _get_many_json(store, keys) if value]


async def _trader_rows_from_store(store: Any, kind: str) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    legacy_rows = await _list_json_by_pattern(store, f"trader:*:{kind}")
    rows.extend(
        [row for row in group if isinstance(row, dict)]
        for group in legacy_rows
        if isinstance(group, list)
    )
    for key in await store.keys(f"stream:trader:*:{kind}"):
        stream_rows = await store.stream(key)
        rows.append([row for row in stream_rows if isinstance(row, dict)])
    return rows


async def _accounts_from_store(
    store: Any,
    timezone_name: str,
    pm_accounts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    keys = await store.keys("trader:*:account")
    accounts = await _get_many_json(store, keys)
    trade_groups = await _trader_rows_from_store(store, "trades")
    trades_by_trading_id: dict[str, list[dict[str, Any]]] = {}
    for group in trade_groups:
        for row in group:
            trading_id = str(row.get("trading_id") or row.get("trader_id") or "")
            if trading_id:
                trades_by_trading_id.setdefault(trading_id, []).append(row)

    async def build_row(account: dict[str, Any]) -> dict[str, Any] | None:
        if not account:
            return None
        trading_id = account["trading_id"]
        config, state, positions = await asyncio.gather(
            store.get_json(f"trader:{trading_id}:config"),
            store.get_json(f"trader:{trading_id}:state"),
            store.get_json(f"trader:{trading_id}:positions"),
        )
        trades = trades_by_trading_id.get(trading_id, [])
        config = config or {}
        state = state or {}
        positions = positions or []
        marked_value = _positions_marked_value(positions if isinstance(positions, list) else [])
        available_cash = _float_or_zero(account.get("available_cash"))
        row = {
            "id": trading_id,
            "mode": account["mode"],
            "strategy_name": config.get("strategy_name", "football_score_delay_trade"),
            "strategy_params": config.get("strategy_params", {}),
            "retracement": float(config.get("strategy_params", {}).get("retracement", 0.05)),
            "initial_balance": account["initial_balance"],
            "affect_sports": config.get("affect_sports", ["football"]),
            "total_assets": available_cash + marked_value,
            "available_cash": available_cash,
            "today_profit": _today_realized_profit(trades, timezone_name),
            "position_count": len(positions) if isinstance(positions, list) else account["position_count"],
            "win_rate": 0,
            "is_running": state.get("status") == "running",
            "account_alias": account.get("account_alias"),
        }
        return _apply_pm_account_source(row, pm_accounts)

    built_rows = await asyncio.gather(
        *(build_row(account) for account in accounts if isinstance(account, dict))
    )
    rows.extend(row for row in built_rows if row is not None)
    return rows


def _accounts_from_trader(
    trader: Any,
    timezone_name: str,
    pm_accounts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot in trader.list_tradings():
        account = trader.get_account(snapshot.trading_id)
        row = {
            "id": snapshot.trading_id,
            "mode": snapshot.mode,
            "strategy_name": snapshot.strategy_name,
            "strategy_params": snapshot.strategy_params,
            "retracement": float(snapshot.strategy_params.get("retracement", 0.05)),
            "initial_balance": account.initial_balance,
            "affect_sports": snapshot.affect_sports,
            "total_assets": account.equity,
            "available_cash": account.available_cash,
            "today_profit": _today_realized_profit(trader.get_trades(snapshot.trading_id), timezone_name),
            "position_count": account.position_count,
            "win_rate": 0,
            "is_running": snapshot.status == "running",
            "account_alias": account.account_alias,
        }
        rows.append(_apply_pm_account_source(row, pm_accounts))
    return rows


async def _pm_accounts_by_alias(request: Request) -> dict[str, dict[str, Any]]:
    try:
        rows = await public_pm_accounts_with_balances(request.app.state.settings)
    except ValueError:
        return {}
    return {str(row.get("id") or ""): row for row in rows if row.get("id")}


def _apply_pm_account_source(
    row: dict[str, Any],
    pm_accounts: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    if row.get("mode") != "real" or not pm_accounts:
        return row
    pm = pm_accounts.get(str(row.get("account_alias") or ""))
    if not pm:
        return row
    available = _float_or_zero(pm.get("available_funds"))
    total = _float_or_zero(pm.get("total_funds"))
    position_funds = _float_or_zero(pm.get("position_funds"))
    position_count = int(pm.get("position_count") or int(position_funds > 0))
    return {
        **row,
        "initial_balance": total,
        "total_assets": total,
        "available_cash": available,
        "position_count": position_count,
        "today_profit": 0.0,
    }


def _today_realized_profit(trades: list[dict[str, Any]], timezone_name: str, now_utc: datetime | None = None) -> float:
    zone = ZoneInfo(timezone_name)
    now = now_utc or datetime.now(UTC)
    today = now.astimezone(zone).date()
    total = 0.0
    for trade in trades:
        if str(trade.get("side") or trade.get("action") or "").lower() != "sell":
            continue
        ts = _parse_datetime(str(trade.get("ts_utc") or ""))
        if ts is None or ts.astimezone(zone).date() != today:
            continue
        total += _float_or_zero(trade.get("profit"))
    return round(total, 2)


async def _enrich_trading_rows(store: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    guids = sorted({str(row.get("guid") or "") for row in rows if row.get("guid")})
    if not guids:
        return rows
    matches = await _get_many_json(store, [f"pm:match:{guid}" for guid in guids])
    by_guid = {guid: match for guid, match in zip(guids, matches, strict=False) if isinstance(match, dict)}
    return [_enrich_trading_row(row, by_guid.get(str(row.get("guid") or ""))) for row in rows]


def _enrich_trading_row(row: dict[str, Any], pm: dict[str, Any] | None) -> dict[str, Any]:
    if not pm:
        return row
    outcome_key = str(row.get("outcome_key") or "")
    enriched = dict(row)
    if not enriched.get("slug"):
        enriched["slug"] = pm.get("slug") or row.get("guid")
    label, team_name = _outcome_display(pm, outcome_key)
    if not enriched.get("team_label"):
        enriched["team_label"] = label
    if not enriched.get("team_name"):
        enriched["team_name"] = team_name
    return enriched


def _pm_row_matches_match(row: dict[str, Any], match_id: str, pm: dict[str, Any] | None) -> bool:
    if row.get("guid") == match_id:
        return True
    if not pm:
        return False
    condition_id = str(pm.get("condition_id") or "")
    slug = str(pm.get("slug") or "")
    row_condition = str(row.get("condition_id") or row.get("conditionId") or "")
    row_slug = str(row.get("slug") or row.get("eventSlug") or "")
    return bool((condition_id and row_condition == condition_id) or (slug and row_slug == slug))


def _outcome_display(pm: dict[str, Any], outcome_key: str) -> tuple[str, str]:
    if outcome_key == "home":
        return "Home", str(pm.get("home_team") or "")
    if outcome_key == "away":
        return "Away", str(pm.get("away_team") or "")
    if outcome_key == "draw":
        return "Draw", ""
    return outcome_key, ""


async def _get_many_json(store: Any, keys: list[str]) -> list[Any | None]:
    if hasattr(store, "get_many_json"):
        return await store.get_many_json(keys)
    return [await store.get_json(key) for key in keys]


def _passes_current_match_filter(
    pm: dict[str, Any],
    collector_settings: CollectorSettings,
    timezone_name: str,
    now_utc: str | datetime | None = None,
) -> bool:
    # 展示层也按当前采集条件过滤；已开赛跨零点的比赛不能因为日期变成昨天就消失。
    if _float_or_zero(pm.get("total_volume")) < collector_settings.football_volume_threshold_k * 1000:
        return False
    start = _parse_datetime(str(pm.get("start_time_utc") or ""))
    if start is None:
        return False
    zone = ZoneInfo(timezone_name)
    now = _parse_datetime(now_utc) if isinstance(now_utc, str) else now_utc
    if now is None:
        now = datetime.now(UTC)
    today = now.astimezone(zone).date()
    match_date = start.astimezone(zone).date()
    if today <= match_date < today + timedelta(days=2):
        return True
    return _is_live_status(pm.get("status")) and match_date == today - timedelta(days=1)


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


def _latest_timestamp(*values: str | None) -> str | None:
    latest_value: str | None = None
    latest_dt: datetime | None = None
    for value in values:
        parsed = _parse_datetime(str(value or ""))
        if parsed is None:
            continue
        if latest_dt is None or parsed > latest_dt:
            latest_dt = parsed
            latest_value = value
    return latest_value


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


def _positions_marked_value(positions: list[Any]) -> float:
    total = 0.0
    for position in positions:
        if not isinstance(position, dict):
            continue
        shares = _float_or_zero(position.get("shares"))
        bid = position.get("current_bid1")
        if bid is not None:
            total += shares * _float_or_zero(bid)
        else:
            total += _float_or_zero(position.get("cost_basis"))
    return total
