import asyncio

import httpx
import pytest

from app.collector import Collector, StaticGSHttpClient
from app.listener import BroadcastHub, Listener
from app.polymarket import (
    PMGammaHttpClient,
    PMUserWsSource,
    normalize_pm_market_ws_payloads,
    normalize_pm_sports_ws_payload,
    normalize_pm_user_ws_payload,
)
from app.store import MemoryStore

from .fixtures import GS_D1, GS_HOME


PM_GAMMA_EVENTS = [
    {
        "id": "pm-real-1",
        "slug": "epl-arsenal-chelsea-2026-05-01",
        "title": "Arsenal vs Chelsea",
        "volume": 250000,
        "startTime": "2026-05-01T19:00:00Z",
        "closed": False,
        "gameId": 9001,
        "tags": [{"slug": "soccer"}],
        "markets": [
            {
                "id": "market-1",
                "conditionId": "cond-real-1",
                "question": "Arsenal vs Chelsea Moneyline",
                "outcomes": '["Arsenal","Draw","Chelsea"]',
                "clobTokenIds": '["asset-real-home","asset-real-draw","asset-real-away"]',
                "outcomePrices": '["0.45","0.28","0.31"]',
                "volume": 120000,
            }
        ],
    }
]


PM_BINARY_MONEYLINE_EVENT = {
    "id": "pm-binary-1",
    "slug": "epl-lee-bur-2026-05-01",
    "title": "Leeds United FC vs. Burnley FC",
    "volume": 133472.35,
    "endDate": "2026-05-01T19:00:00Z",
    "closed": False,
    "tags": [{"slug": "soccer"}],
    "markets": [
        {
            "id": "home-market",
            "conditionId": "cond-home",
            "question": "Will Leeds United FC win on 2026-05-01?",
            "groupItemTitle": "Leeds United FC",
            "sportsMarketType": "moneyline",
            "outcomes": '["Yes","No"]',
            "clobTokenIds": '["asset-home-yes","asset-home-no"]',
            "outcomePrices": '["0.705","0.295"]',
            "bestBid": 0.70,
            "bestAsk": 0.71,
            "gameStartTime": "2026-05-01 19:00:00+00",
            "volume": "36303.37",
        },
        {
            "id": "draw-market",
            "conditionId": "cond-draw",
            "question": "Will Leeds United FC vs. Burnley FC end in a draw?",
            "groupItemTitle": "Draw (Leeds United FC vs. Burnley FC)",
            "sportsMarketType": "moneyline",
            "outcomes": '["Yes","No"]',
            "clobTokenIds": '["asset-draw-yes","asset-draw-no"]',
            "outcomePrices": '["0.185","0.815"]',
            "bestBid": 0.18,
            "bestAsk": 0.19,
            "gameStartTime": "2026-05-01 19:00:00+00",
            "volume": "17979.93",
        },
        {
            "id": "away-market",
            "conditionId": "cond-away",
            "question": "Will Burnley FC win on 2026-05-01?",
            "groupItemTitle": "Burnley FC",
            "sportsMarketType": "moneyline",
            "outcomes": '["Yes","No"]',
            "clobTokenIds": '["asset-away-yes","asset-away-no"]',
            "outcomePrices": '["0.105","0.895"]',
            "bestBid": 0.10,
            "bestAsk": 0.11,
            "gameStartTime": "2026-05-01 19:00:00+00",
            "volume": "79247.33",
        },
    ],
}


@pytest.mark.asyncio
async def test_pm_gamma_http_client_fetches_soccer_events_and_normalizes_moneyline():
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json=PM_GAMMA_EVENTS)

    client = PMGammaHttpClient(
        base_url="https://gamma-api.polymarket.test/events",
        transport=httpx.MockTransport(handler),
    )

    rows = await client.fetch_events()
    await client.close()

    assert rows[0]["id"] == "pm-real-1"
    assert rows[0]["sport"] == "football"
    assert rows[0]["home_team"] == "Arsenal"
    assert rows[0]["away_team"] == "Chelsea"
    assert rows[0]["market"]["tokens"] == {
        "home": "asset-real-home",
        "draw": "asset-real-draw",
        "away": "asset-real-away",
    }
    assert rows[0]["market"]["prices"]["home"]["ask"] == 0.45
    assert rows[0]["market"]["condition_id"] == "cond-real-1"
    assert "tag_slug=soccer" in requested_urls[0]


@pytest.mark.asyncio
async def test_pm_gamma_http_client_fetches_late_page_soccer_events():
    pages = [
        [
            {**PM_GAMMA_EVENTS[0], "id": f"old-{page}-{index}", "slug": f"old-{page}-{index}"}
            for index in range(200)
        ]
        for page in range(6)
    ]
    pages.append([PM_BINARY_MONEYLINE_EVENT])
    requested_offsets: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_offsets.append(request.url.params.get("offset"))
        offset = int(request.url.params.get("offset", "0"))
        page = offset // 200
        return httpx.Response(200, json=pages[page] if page < len(pages) else [])

    client = PMGammaHttpClient(
        base_url="https://gamma-api.polymarket.test/events",
        transport=httpx.MockTransport(handler),
    )

    rows = await client.fetch_events()
    await client.close()

    assert any(row["slug"] == "epl-lee-bur-2026-05-01" for row in rows)
    assert requested_offsets[-1] == "1200"


def test_pm_gamma_parser_maps_binary_moneyline_markets_to_three_outcomes():
    from app.polymarket import normalize_pm_gamma_event

    row = normalize_pm_gamma_event(PM_BINARY_MONEYLINE_EVENT)

    assert row is not None
    assert row["start_time"] == "2026-05-01 19:00:00+00"
    assert row["volume"]["moneyline"] == pytest.approx(133530.63)
    assert row["volume"]["total"] == pytest.approx(133472.35)
    assert row["market"]["tokens"] == {
        "home": "asset-home-yes",
        "draw": "asset-draw-yes",
        "away": "asset-away-yes",
    }
    assert row["market"]["prices"]["home"] == {"bid": 0.7, "ask": 0.71}
    assert row["market"]["prices"]["draw"] == {"bid": 0.18, "ask": 0.19}
    assert row["market"]["prices"]["away"] == {"bid": 0.1, "ask": 0.11}


@pytest.mark.asyncio
async def test_pm_gamma_http_client_uses_series_title_without_leaking_raw_json():
    payload = [
        {
            **PM_GAMMA_EVENTS[0],
            "series": [
                {
                    "id": "10364",
                    "title": "Indian Super League",
                    "slug": "indian-super-league",
                }
            ],
            "league": None,
            "category": None,
        }
    ]
    client = PMGammaHttpClient(
        base_url="https://gamma-api.polymarket.test/events",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
    )

    rows = await client.fetch_events()
    await client.close()

    assert rows[0]["league"] == "Indian Super League"


@pytest.mark.asyncio
async def test_collector_can_run_with_real_pm_http_client_without_goalserve_match():
    store = MemoryStore()
    client = PMGammaHttpClient(
        base_url="https://gamma-api.polymarket.test/events",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=PM_GAMMA_EVENTS)),
    )
    collector = Collector(store=store, pm_client=client, gs_client=StaticGSHttpClient(home=[], d1=[]))

    report = await collector.collect_once()
    await client.close()

    assert report["pm_seen"] == 1
    assert report["matched"] == 0
    assert report["pending"] == 1
    assert await store.get_text("idx:pm:slug:epl-arsenal-chelsea-2026-05-01") == "guid-pm-real-1"
    assert await store.get_text("idx:pm:game:9001") == "guid-pm-real-1"


@pytest.mark.asyncio
async def test_pm_sports_ws_payload_can_resolve_by_slug_and_update_pm_state():
    store = MemoryStore()
    client = PMGammaHttpClient(
        base_url="https://gamma-api.polymarket.test/events",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=PM_GAMMA_EVENTS)),
    )
    collector = Collector(store=store, pm_client=client, gs_client=StaticGSHttpClient(home=GS_HOME, d1=GS_D1))
    await collector.collect_once()
    await client.close()
    listener = Listener(store=store, broadcaster=BroadcastHub())

    normalized = normalize_pm_sports_ws_payload(
        {
            "eventSlug": "epl-arsenal-chelsea-2026-05-01",
            "homeScore": 1,
            "awayScore": 0,
            "matchStatus": "LIVE",
            "clock": "34:12",
        }
    )
    event = await listener.process_payload("pm_sports", normalized)
    pm_match = await store.get_json("pm:match:guid-pm-real-1")

    assert event is not None
    assert event["guid"] == "guid-pm-real-1"
    assert pm_match["score_home"] == 1
    assert pm_match["score_away"] == 0
    assert pm_match["match_time"] == "34:12"


def test_pm_market_ws_payloads_normalize_book_and_price_change_messages():
    rows = normalize_pm_market_ws_payloads(
        {
            "event_type": "price_change",
            "timestamp": "1710000000000",
            "price_changes": [
                {"asset_id": "asset-real-home", "best_bid": "0.44", "best_ask": "0.46"},
                {"asset_id": "asset-real-away", "best_bid": "0.30", "best_ask": "0.32"},
            ],
        }
    )
    book_rows = normalize_pm_market_ws_payloads(
        {
            "event_type": "book",
            "asset_id": "asset-real-home",
            "bids": [{"price": "0.43", "size": "10"}],
            "asks": [{"price": "0.45", "size": "8"}],
            "timestamp": "1710000000001",
        }
    )

    assert rows == [
        {"asset_id": "asset-real-home", "bid": 0.44, "ask": 0.46, "ts": "1710000000000"},
        {"asset_id": "asset-real-away", "bid": 0.3, "ask": 0.32, "ts": "1710000000000"},
    ]
    assert book_rows == [
        {"asset_id": "asset-real-home", "bid": 0.43, "ask": 0.45, "ts": "1710000000001"}
    ]


def test_pm_user_ws_payload_normalizes_orders_and_fills_without_credentials():
    order = normalize_pm_user_ws_payload(
        {
            "event_type": "order",
            "id": "order-1",
            "asset_id": "asset-real-home",
            "timestamp": "2026-05-03T12:00:00Z",
        },
        "pm-main",
    )
    fill = normalize_pm_user_ws_payload(
        {
            "event_type": "trade",
            "id": "fill-1",
            "asset_id": "asset-real-home",
            "timestamp": "2026-05-03T12:00:01Z",
        },
        "pm-main",
    )

    assert order["account_alias"] == "pm-main"
    assert order["orders"][0]["id"] == "order-1"
    assert fill["fills"][0]["id"] == "fill-1"
    assert "auth" not in order


@pytest.mark.asyncio
async def test_pm_user_ws_source_reports_disconnected_only_after_all_accounts_drop():
    class Account:
        def __init__(self, alias):
            self.alias = alias
            self.has_api_credentials = True

    queue = asyncio.Queue()
    source = PMUserWsSource(store=MemoryStore(), accounts=[Account("pm-a"), Account("pm-b")])
    account_a, account_b = source._accounts

    await source._mark_account_connected(account_a, queue)
    await source._mark_account_connected(account_b, queue)
    await source._mark_account_disconnected(account_a, queue, RuntimeError("drop"))
    await source._mark_account_disconnected(account_b, queue, RuntimeError("drop"))

    rows = [await queue.get(), await queue.get(), await queue.get()]
    assert rows[0]["__connection_status__"] == "connected"
    assert rows[1]["__connection_status__"] == "connected"
    assert rows[2]["__connection_status__"] == "disconnected"
    assert rows[2]["account_alias"] == "pm-b"


def test_pm_sports_ws_payload_uses_period_and_elapsed_as_match_time():
    normalized = normalize_pm_sports_ws_payload(
        {
            "gameId": 90094752,
            "slug": "lal-vil-lev-2026-05-02",
            "status": "InProgress",
            "score": "1-0",
            "period": "2H",
            "elapsed": "05:09",
            "last_update": "2026-05-02T13:05:09Z",
        }
    )

    assert normalized["game_id"] == "90094752"
    assert normalized["status"] == "live"
    assert normalized["match_time"] == "2H 05:09"
    assert normalized["score"] == {"home": 1, "away": 0}


def test_pm_sports_ws_payload_ignores_non_football_sports():
    normalized = normalize_pm_sports_ws_payload(
        {
            "gameId": 5426482,
            "leagueAbbreviation": "challenger",
            "homeTeam": "Hubert Hurkacz",
            "awayTeam": "Roman Andres Burruchaga",
            "status": "inprogress",
            "eventState": {"type": "tennis", "score": "4-6, 4-5"},
        }
    )

    assert normalized is None


def test_pm_gamma_http_parser_rejects_futures_and_accepts_slug_match_shape():
    futures = {
        "id": "pm-futures",
        "slug": "english-premier-league-winner",
        "title": "English Premier League Winner",
        "volume": 250000,
        "closed": False,
        "tags": [{"slug": "soccer"}],
        "markets": [
            {
                "id": "market-futures",
                "conditionId": "cond-futures",
                "outcomes": '["Arsenal","Liverpool"]',
                "clobTokenIds": '["asset-a","asset-b"]',
                "outcomePrices": '["0.45","0.55"]',
            }
        ],
    }
    slug_match = {
        **PM_GAMMA_EVENTS[0],
        "id": "pm-slug-match",
        "slug": "bra-cre-pal-2026-05-10",
        "title": "",
    }

    from app.polymarket import normalize_pm_gamma_event

    assert normalize_pm_gamma_event(futures) is None
    assert normalize_pm_gamma_event(slug_match)["home_team"] == "Cre"
    assert normalize_pm_gamma_event(slug_match)["away_team"] == "Pal"


def test_pm_gamma_parser_extracts_team_logos_from_event_teams():
    from app.polymarket import normalize_pm_gamma_event

    row = normalize_pm_gamma_event(
        {
            **PM_BINARY_MONEYLINE_EVENT,
            "title": "Villarreal CF vs. Levante UD",
            "teams": [
                {
                    "name": "Villarreal CF",
                    "logo": "https://polymarket-upload.s3.us-east-2.amazonaws.com/Villarreal CF.png",
                },
                {
                    "name": "Levante UD",
                    "logo": "https://polymarket-upload.s3.us-east-2.amazonaws.com/Levante UD.png",
                },
            ],
        }
    )

    assert row is not None
    assert row["team_logos"] == {
        "home": "https://polymarket-upload.s3.us-east-2.amazonaws.com/Villarreal CF.png",
        "away": "https://polymarket-upload.s3.us-east-2.amazonaws.com/Levante UD.png",
    }
