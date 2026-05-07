"""Polymarket 接入：PM HTTP 采集和可选 sports/market WS source。"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import httpx
from py_clob_client.client import ClobClient
from websockets.legacy.client import connect as legacy_ws_connect


PM_GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
PM_SPORTS_WS_URL = "wss://sports-api.polymarket.com/ws"
PM_MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PM_USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"


class PMClobQuoteClient:
    """PM CLOB 公共 orderbook 报价客户端；只读，不需要账户授权。"""

    def __init__(self, *, host: str = "https://clob.polymarket.com"):
        self._client = ClobClient(host)

    async def get_quote(self, asset_id: str) -> dict[str, float | None]:
        return await asyncio.to_thread(self._get_quote_sync, asset_id)

    def _get_quote_sync(self, asset_id: str) -> dict[str, float | None]:
        book = self._client.get_order_book(asset_id)
        return {
            "bid1": _best_bid(book.bids or []),
            "ask1": _best_ask(book.asks or []),
        }


class PMGammaHttpClient:
    """Polymarket Gamma HTTP 足球采集客户端。

    Collector 只需要标准化后的足球比赛列表；这里把 Gamma event/market
    的多种字符串 JSON 字段转成 Collector 已经认识的内部结构。
    """

    def __init__(
        self,
        *,
        base_url: str = PM_GAMMA_EVENTS_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 15.0,
        page_size: int = 200,
        max_pages: int = 10,
    ):
        self._base_url = base_url
        self._page_size = page_size
        self._max_pages = max_pages
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout_seconds, follow_redirects=True)

    async def fetch_events(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(self._max_pages):
            response = await self._client.get(
                self._base_url,
                params={
                    "closed": "false",
                    "limit": self._page_size,
                    "offset": page * self._page_size,
                    "tag_slug": "soccer",
                },
            )
            response.raise_for_status()
            payload = response.json()
            events = payload if isinstance(payload, list) else payload.get("events", [])
            if not events:
                break
            for event in events:
                if isinstance(event, dict):
                    normalized = normalize_pm_gamma_event(event)
                    if normalized is not None:
                        rows.append(normalized)
            if len(events) < self._page_size:
                break
        return rows

    async def close(self) -> None:
        await self._client.aclose()


class PMSportsWsSource:
    """PM sports WS 源；输出 Listener 可处理的 pm_sports payload。"""

    name = "pm_sports"

    def __init__(self, *, endpoint: str = PM_SPORTS_WS_URL):
        self._endpoint = endpoint

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        async with legacy_ws_connect(self._endpoint, ping_interval=20, ping_timeout=20, max_queue=2048) as websocket:
            yield {"__connection_status__": "connected"}
            async for raw in websocket:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="ignore")
                if isinstance(raw, str) and raw.lower() == "ping":
                    await websocket.send("pong")
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for row in _as_list(payload):
                    if isinstance(row, dict):
                        normalized = normalize_pm_sports_ws_payload(row)
                        if normalized:
                            yield normalized


class PMMarketWsSource:
    """PM market WS 源；根据 Redis 里的 PM asset 索引订阅盘口。"""

    name = "pm_market"

    def __init__(self, *, store: Any, endpoint: str = PM_MARKET_WS_URL):
        self._store = store
        self._endpoint = endpoint
        self._subscribed_asset_ids: set[str] = set()

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        async with legacy_ws_connect(self._endpoint, ping_interval=20, ping_timeout=20, max_queue=2048) as websocket:
            self._subscribed_asset_ids.clear()
            await self.subscribe_current_assets(websocket)
            yield {"__connection_status__": "connected"}
            subscribe_task = asyncio.create_task(self._refresh_subscriptions(websocket))
            try:
                async for raw in websocket:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="ignore")
                    if isinstance(raw, str) and raw.lower() == "ping":
                        await websocket.send("pong")
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    rows = payload if isinstance(payload, list) else [payload]
                    for row in rows:
                        if isinstance(row, dict):
                            for normalized in self.normalize_subscribed_payloads(row):
                                yield normalized
            finally:
                subscribe_task.cancel()
                with suppress(asyncio.CancelledError):
                    await subscribe_task

    async def subscribe_current_assets(self, websocket: Any) -> int:
        asset_ids = [key.rsplit(":", 1)[-1] for key in await self._store.keys("idx:pm:asset:*")]
        new_asset_ids = [asset_id for asset_id in asset_ids if asset_id not in self._subscribed_asset_ids]
        for offset in range(0, len(new_asset_ids), 250):
            chunk = new_asset_ids[offset : offset + 250]
            if not chunk:
                continue
            await websocket.send(
                json.dumps(
                    {
                        "type": "market",
                        "assets_ids": chunk,
                        "custom_feature_enabled": True,
                    }
                )
            )
            self._subscribed_asset_ids.update(chunk)
        return len(new_asset_ids)

    async def _refresh_subscriptions(self, websocket: Any) -> None:
        while True:
            await asyncio.sleep(30)
            await self.subscribe_current_assets(websocket)

    def normalize_subscribed_payloads(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """只交付当前订阅的 YES asset，过滤 PM 同包带出的反向 token。"""

        return [
            row
            for row in normalize_pm_market_ws_payloads(payload)
            if row.get("asset_id") in self._subscribed_asset_ids
        ]


class PMUserWsSource:
    """PM user WS 源；只在服务端使用 API credentials 订阅用户订单/成交。"""

    name = "pm_user"

    def __init__(
        self,
        *,
        store: Any,
        accounts: list[Any],
        endpoint: str = PM_USER_WS_URL,
        reconnect_delay_seconds: float = 1.0,
    ):
        self._store = store
        self._accounts = [account for account in accounts if getattr(account, "has_api_credentials", False)]
        self._endpoint = endpoint
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._connected_aliases: set[str] = set()

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        if not self._accounts:
            return
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._connected_aliases.clear()
        tasks = [asyncio.create_task(self._listen_account_forever(account, queue)) for account in self._accounts]
        try:
            while True:
                yield await queue.get()
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task

    async def _listen_account_forever(self, account: Any, queue: asyncio.Queue[dict[str, Any]]) -> None:
        while True:
            try:
                await self._listen_account_once(account, queue)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._mark_account_disconnected(account, queue, exc)
            else:
                await self._mark_account_disconnected(account, queue, None)
            await asyncio.sleep(self._reconnect_delay_seconds)

    async def _listen_account_once(self, account: Any, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with legacy_ws_connect(self._endpoint, ping_interval=None, ping_timeout=None, max_queue=2048) as websocket:
            await self._subscribe_current_markets(websocket, account, initial=True)
            await self._mark_account_connected(account, queue)
            heartbeat_task = asyncio.create_task(self._send_heartbeat(websocket))
            subscribe_task = asyncio.create_task(self._refresh_subscriptions(websocket, account))
            try:
                async for raw in websocket:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="ignore")
                    if isinstance(raw, str) and raw.upper() == "PONG":
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for row in _as_list(payload):
                        if not isinstance(row, dict):
                            continue
                        normalized = normalize_pm_user_ws_payload(row, account.alias)
                        if normalized:
                            await queue.put(normalized)
            finally:
                heartbeat_task.cancel()
                subscribe_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
                with suppress(asyncio.CancelledError):
                    await subscribe_task

    async def _mark_account_connected(self, account: Any, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._connected_aliases.add(str(account.alias))
        await queue.put({"__connection_status__": "connected", "account_alias": account.alias})

    async def _mark_account_disconnected(
        self,
        account: Any,
        queue: asyncio.Queue[dict[str, Any]],
        exc: Exception | None,
    ) -> None:
        self._connected_aliases.discard(str(account.alias))
        if self._connected_aliases:
            return
        row = {"__connection_status__": "disconnected", "account_alias": account.alias}
        if exc is not None:
            row["error"] = exc.__class__.__name__
        await queue.put(row)

    async def _subscribe_current_markets(self, websocket: Any, account: Any, *, initial: bool = False) -> None:
        market_ids = [
            str((row or {}).get("condition_id"))
            for row in await self._store.get_many_json(await self._store.keys("pm:match:*"))
            if isinstance(row, dict) and row.get("condition_id")
        ]
        payload: dict[str, Any] = {
            "auth": {
                "apiKey": account.api_key,
                "secret": account.api_secret,
                "passphrase": account.api_passphrase,
            },
            "type": "user",
        }
        if market_ids:
            payload["markets"] = sorted(set(market_ids))
        if not initial:
            payload["operation"] = "subscribe"
        await websocket.send(json.dumps(payload))

    async def _refresh_subscriptions(self, websocket: Any, account: Any) -> None:
        while True:
            await asyncio.sleep(30)
            await self._subscribe_current_markets(websocket, account)

    async def _send_heartbeat(self, websocket: Any) -> None:
        while True:
            await asyncio.sleep(10)
            await websocket.send("PING")


def normalize_pm_user_ws_payload(payload: dict[str, Any], account_alias: str) -> dict[str, Any] | None:
    event_type = str(payload.get("event_type") or payload.get("type") or "").lower()
    ts = _text(payload, "timestamp", "created_at", "updated_at", "ts")
    row = {
        "account_alias": account_alias,
        "ts": ts,
        "message_id": _text(payload, "id", "message_id") or event_type,
        "raw": payload,
    }
    if event_type == "order":
        row["orders"] = [payload]
        return row
    if event_type in {"trade", "fill"}:
        row["fills"] = [payload]
        return row
    if any(key in payload for key in ("balance", "available_cash")):
        row["balance"] = payload.get("balance")
        row["available_cash"] = payload.get("available_cash")
        return row
    return None


def normalize_pm_gamma_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if _is_closed(event) or not _is_soccer(event):
        return None
    slug = str(event.get("slug") or "")
    if is_secondary_sports_market_slug(slug):
        return None
    home_team, away_team = _split_teams(str(event.get("title") or ""), slug)
    if not home_team or not away_team:
        return None
    market = _select_moneyline_market(event.get("markets") or [], home_team, away_team)
    if market is None:
        return None
    start_time = _start_time(event, market)
    if not start_time:
        return None
    moneyline_volume = _float(market.get("volume"), 0.0)
    event_total_volume = _float(event.get("volume"), 0.0)
    return {
        "id": str(event.get("id") or slug or market.get("id")),
        "slug": str(slug or event.get("id") or market.get("id")),
        "sport": "football",
        "league": _league(event),
        "start_time": start_time,
        "home_team": home_team,
        "away_team": away_team,
        "team_logos": _team_logos(event, home_team, away_team),
        "status": "finished" if bool(event.get("ended")) else ("live" if _is_live(event) else "scheduled"),
        "score": _score(event),
        "game_id": event.get("gameId") or event.get("game_id") or event.get("polymarket_game_id"),
        "market": market,
        "volume": {
            "moneyline": moneyline_volume,
            "total": event_total_volume if event_total_volume > 0 else moneyline_volume,
        },
        "raw": event,
    }


def normalize_pm_sports_ws_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not _is_pm_sports_football(payload):
        return None
    home, away = _score_pair(payload)
    return {
        "event_id": _text(payload, "event_id", "eventId", "id"),
        "slug": _text(payload, "slug", "eventSlug"),
        "game_id": _text(payload, "gameId", "game_id", "metadataGameId", "metadata_game_id"),
        "status": _canonical_sports_status(_text(payload, "status", "state", "matchStatus")),
        "match_time": _pm_sports_match_time(payload),
        "score": {"home": home, "away": away},
        "ts": _text(payload, "last_update", "lastUpdate", "timestamp", "ts"),
        "message_id": _text(payload, "message_id", "id", "slug", "eventSlug"),
        "raw": payload,
    }


def _is_pm_sports_football(payload: dict[str, Any]) -> bool:
    event_state = payload.get("eventState") if isinstance(payload.get("eventState"), dict) else {}
    sport_type = str(event_state.get("type") or payload.get("sport") or payload.get("sportType") or "").lower()
    if sport_type:
        return sport_type in {"soccer", "football"}
    league = str(payload.get("leagueAbbreviation") or payload.get("league") or "").lower()
    if league in {"nba", "wnba", "ncaab", "mlb", "nhl", "challenger", "wta challenger", "atp", "wta"}:
        return False
    return True


def _canonical_sports_status(value: Any) -> str | None:
    text = str(value or "").strip()
    normalized = text.lower().replace("_", "").replace("-", "").replace(" ", "")
    if normalized in {"live", "inplay", "inprogress", "started", "active"}:
        return "live"
    if normalized in {"finished", "final", "ended", "closed", "complete", "completed", "ft", "fulltime"}:
        return "finished"
    if normalized in {"scheduled", "prematch", "pre", "notstarted", "upcoming"}:
        return "scheduled"
    return text or None


def normalize_pm_market_ws_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # PM market channel 有多种事件类型，最终都压成 asset_id + bid/ask。
    event_type = str(payload.get("event_type") or payload.get("event") or payload.get("type") or "").lower()
    timestamp = str(payload.get("timestamp") or payload.get("ts") or "")
    if event_type == "price_change":
        rows = []
        for change in payload.get("price_changes") or []:
            if isinstance(change, dict):
                rows.append(
                    {
                        "asset_id": str(change.get("asset_id") or ""),
                        "bid": _optional_float(change.get("best_bid")),
                        "ask": _optional_float(change.get("best_ask")),
                        "ts": timestamp,
                    }
                )
        return [row for row in rows if row["asset_id"]]
    if event_type == "book":
        return [
            {
                "asset_id": str(payload.get("asset_id") or ""),
                "bid": _best_bid(payload.get("bids") or payload.get("buys") or []),
                "ask": _best_ask(payload.get("asks") or payload.get("sells") or []),
                "ts": timestamp,
            }
        ]
    if event_type == "best_bid_ask":
        return [
            {
                "asset_id": str(payload.get("asset_id") or payload.get("market") or ""),
                "bid": _optional_float(payload.get("best_bid")),
                "ask": _optional_float(payload.get("best_ask")),
                "ts": timestamp,
            }
        ]
    return []


def _select_moneyline_market(markets: list[Any], home_team: str, away_team: str) -> dict[str, Any] | None:
    binary = _select_binary_moneyline_markets(markets, home_team, away_team)
    if binary is not None:
        return binary
    for raw in markets:
        if not isinstance(raw, dict):
            continue
        outcomes = _loads_list(raw.get("outcomes"))
        tokens = _loads_list(raw.get("clobTokenIds"))
        prices = _loads_list(raw.get("outcomePrices"))
        if len(outcomes) < 2 or len(tokens) < 2:
            continue
        mapping = _outcome_mapping(outcomes, home_team, away_team)
        if "home" not in mapping or "away" not in mapping:
            continue
        return {
            "condition_id": raw.get("conditionId") or raw.get("condition_id"),
            "tokens": {
                outcome: tokens[index] if index < len(tokens) else None
                for outcome, index in mapping.items()
            },
            "prices": {
                outcome: {"bid": _price(prices, index), "ask": _price(prices, index)}
                for outcome, index in mapping.items()
            },
            "raw": raw,
        }
    return None


def _select_binary_moneyline_markets(markets: list[Any], home_team: str, away_team: str) -> dict[str, Any] | None:
    grouped: dict[str, dict[str, Any]] = {}
    for raw in markets:
        if not isinstance(raw, dict):
            continue
        outcomes = [str(item).lower() for item in _loads_list(raw.get("outcomes"))]
        tokens = _loads_list(raw.get("clobTokenIds"))
        if len(outcomes) < 2 or len(tokens) < 2 or outcomes[0] != "yes":
            continue
        outcome = _binary_moneyline_outcome(raw, home_team, away_team)
        if outcome is None:
            continue
        prices = _loads_list(raw.get("outcomePrices"))
        grouped[outcome] = {
            "token": tokens[0],
            "bid": _optional_float(raw.get("bestBid")),
            "ask": _optional_float(raw.get("bestAsk")),
            "price": _price(prices, 0),
            "condition_id": raw.get("conditionId") or raw.get("condition_id"),
            "volume": _optional_float(raw.get("volume")),
            "raw": raw,
        }
    if "home" not in grouped or "away" not in grouped:
        return None
    raw_markets = [grouped[key]["raw"] for key in ("home", "draw", "away") if key in grouped]
    return {
        "condition_id": grouped["home"]["condition_id"],
        "tokens": {outcome: item["token"] for outcome, item in grouped.items()},
        "prices": {
            outcome: {
                "bid": item["bid"] if item["bid"] is not None else item["price"],
                "ask": item["ask"] if item["ask"] is not None else item["price"],
            }
            for outcome, item in grouped.items()
        },
        "volume": sum(float(item["volume"] or 0.0) for item in grouped.values()),
        "raw": raw_markets[0] if len(raw_markets) == 1 else {"markets": raw_markets},
    }


def _binary_moneyline_outcome(raw: dict[str, Any], home_team: str, away_team: str) -> str | None:
    text = _normalize_team(
        " ".join(
            str(raw.get(key) or "")
            for key in ("groupItemTitle", "question", "slug")
        )
    )
    home_key = _normalize_team(home_team)
    away_key = _normalize_team(away_team)
    if "draw" in text or "tie" in text:
        return "draw"
    if home_key and home_key in text:
        return "home"
    if away_key and away_key in text:
        return "away"
    return None


def _outcome_mapping(outcomes: list[Any], home_team: str, away_team: str) -> dict[str, int]:
    home_key = _normalize_team(home_team)
    away_key = _normalize_team(away_team)
    if not home_key or not away_key:
        return {}
    mapping: dict[str, int] = {}
    for index, value in enumerate(outcomes):
        text = _normalize_team(str(value))
        if text in {"draw", "tie"} or "draw" in text:
            mapping["draw"] = index
        elif text == home_key or home_key in text:
            mapping["home"] = index
        elif text == away_key or away_key in text:
            mapping["away"] = index
    if not mapping and len(outcomes) >= 3:
        mapping = {"home": 0, "draw": 1, "away": 2}
    if "home" not in mapping and len(outcomes) >= 1:
        mapping["home"] = 0
    if "away" not in mapping and len(outcomes) >= 2:
        mapping["away"] = 1 if "draw" not in mapping else 2
    return mapping


def _split_teams(title: str, slug: str) -> tuple[str, str]:
    # Gamma 的 title 最可靠；没有 title 时，只接受带日期的 slug 作为比赛兜底。
    parsed = _split_team_text(title)
    if parsed != ("", ""):
        return parsed
    slug_body = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", slug)
    if slug_body == slug:
        return "", ""
    parsed = _split_team_text(slug_body)
    if parsed != ("", ""):
        return parsed
    parts = [part for part in slug_body.split("-") if part]
    if len(parts) >= 3:
        return parts[-2].title(), parts[-1].title()
    return "", ""


def _split_team_text(value: str) -> tuple[str, str]:
    text = value.replace("-", " ")
    for sep in (" vs. ", " vs ", " v. ", " v "):
        if sep in text:
            left, right = text.split(sep, 1)
            return left.strip().title(), right.strip().title()
    return "", ""


def is_secondary_sports_market_slug(slug: str) -> bool:
    """判断 PM 体育 event slug 是否是同场比赛下的子盘口，不作为独立比赛采集。"""

    normalized = slug.strip().lower()
    if normalized == "more-markets" or normalized.endswith("-more-markets"):
        return True
    return normalized.endswith(("-exact-score", "-correct-score"))


def _score_pair(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    score = payload.get("score")
    if isinstance(score, dict):
        return _int(score.get("home")), _int(score.get("away"))
    if isinstance(score, str):
        matched = re.search(r"(\d+)\s*[-:]\s*(\d+)", score)
        if matched:
            return int(matched.group(1)), int(matched.group(2))
    return _int(payload.get("homeScore")), _int(payload.get("awayScore"))


def _pm_sports_match_time(payload: dict[str, Any]) -> str | None:
    explicit = _text(payload, "clock", "match_time")
    if explicit:
        return explicit
    period = _text(payload, "period")
    elapsed = _text(payload, "elapsed")
    if period and elapsed:
        return f"{period} {elapsed}"
    return period or elapsed


def _score(event: dict[str, Any]) -> dict[str, int | None]:
    home, away = _score_pair(event)
    return {"home": home or 0, "away": away or 0}


def _is_soccer(event: dict[str, Any]) -> bool:
    tags = event.get("tags") or []
    text = " ".join(
        str((tag or {}).get("slug") or (tag or {}).get("label") or tag).lower()
        for tag in tags
    )
    return "soccer" in text or "football" in text or not tags


def _is_closed(event: dict[str, Any]) -> bool:
    return bool(event.get("closed") or event.get("archived"))


def _is_live(event: dict[str, Any]) -> bool:
    status = str(event.get("status") or "").lower()
    return bool(event.get("live")) or status in {"live", "inplay", "in_play"}


def _team_logos(event: dict[str, Any], home_team: str, away_team: str) -> dict[str, str]:
    logos: dict[str, str] = {}
    for team in event.get("teams") or []:
        if not isinstance(team, dict):
            continue
        name = _text(team, "name", "title", "displayName", "abbreviation")
        logo = _text(team, "logo", "image", "icon", "logoUrl", "imageUrl")
        if not name or not logo:
            continue
        normalized_name = _normalize_team(name)
        if _normalize_team(home_team) == normalized_name:
            logos["home"] = logo
        elif _normalize_team(away_team) == normalized_name:
            logos["away"] = logo
    return logos


def _league(event: dict[str, Any]) -> str:
    for key in ("league", "category", "series"):
        value = event.get(key)
        if isinstance(value, dict):
            text = _text(value, "title", "name", "label", "slug")
            if text:
                return text
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    text = _text(item, "title", "name", "label", "slug")
                    if text:
                        return text
        elif value:
            return str(value)
    return ""


def _start_time(event: dict[str, Any], market: dict[str, Any]) -> str:
    raw_market = market.get("raw") or {}
    if isinstance(raw_market.get("markets"), list) and raw_market["markets"]:
        raw_market = raw_market["markets"][0]
    for value in (
        event.get("startTime"),
        event.get("start_time"),
        raw_market.get("eventStartTime"),
        raw_market.get("gameStartTime"),
        raw_market.get("startTime"),
        event.get("gameStartTime"),
        event.get("endDate"),
    ):
        if value:
            return str(value)
    return ""


def _best_bid(levels: list[Any]) -> float | None:
    prices = [_level_price(level) for level in levels]
    prices = [price for price in prices if price is not None]
    return max(prices) if prices else None


def _best_ask(levels: list[Any]) -> float | None:
    prices = [_level_price(level) for level in levels]
    prices = [price for price in prices if price is not None]
    return min(prices) if prices else None


def _level_price(level: Any) -> float | None:
    if isinstance(level, dict):
        return _optional_float(level.get("price"))
    if hasattr(level, "price"):
        return _optional_float(getattr(level, "price"))
    return _optional_float(level)


def _price(values: list[Any], index: int) -> float | None:
    return _optional_float(values[index]) if index < len(values) else None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float) -> float:
    parsed = _optional_float(value)
    return default if parsed is None else parsed


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _loads_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _normalize_team(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _text(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        item = value.get(key)
        if item is not None and str(item).strip():
            return str(item).strip()
    return None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]
