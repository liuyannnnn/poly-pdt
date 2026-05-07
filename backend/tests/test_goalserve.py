import httpx
import pytest

from app.collector import Collector, StaticPMHttpClient
from app.goalserve import GoalserveHttpClient, normalize_goalserve_ws_payload, parse_goalserve_feed
from app.listener import BroadcastHub, Listener
from app.store import MemoryStore

from .fixtures import PM_EVENTS


GOALSERVE_JSON_FEED = {
    "scores": {
        "sport": "soccer",
        "category": {
            "@id": "1204",
            "@name": "Premier League",
            "matches": {
                "match": {
                    "@id": "live-100",
                    "@static_id": "static-100",
                    "@status": "45",
                    "@formatted_date": "01.05.2026",
                    "@time": "19:00",
                    "@commentary_available": "1193",
                    "localteam": {"@name": "Arsenal", "@goals": "0"},
                    "visitorteam": {"@name": "Chelsea", "@goals": "0"},
                    "stat": {
                        "a": "2-1",
                        "yc": "1-2",
                        "rc": "0-1",
                        "corners": "4-3",
                        "shots_on_target": "7-5",
                    },
                    "events": {
                        "event": [
                            {
                                "@type": "Yellow Card",
                                "@minute": "12",
                                "@team": "localteam",
                                "@player": "Rice",
                            },
                            {
                                "@type": "Goal",
                                "@minute": "31",
                                "@team": "localteam",
                                "@result": "[1-0]",
                            },
                        ]
                    },
                    "lineups": {
                        "localteam": {"player": [{"@name": "Saka"}]},
                        "visitorteam": {"player": [{"@name": "Palmer"}]},
                    },
                    "venue": {"@name": "Emirates Stadium"},
                }
            },
        },
    }
}


def test_goalserve_json_feed_parser_uses_stat_a_and_extracts_match_data():
    rows = parse_goalserve_feed(GOALSERVE_JSON_FEED)

    assert rows == [
        {
            "match_id": "static-100",
            "pregame_id": "static-100",
            "inplay_id": "live-100",
            "static_id": "static-100",
            "league": "Premier League",
            "league_id": "1204",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "start_time_utc": "2026-05-01T19:00:00Z",
            "status": "45",
            "score": {"home": 2, "away": 1},
            "match_time": "45",
            "period": "",
            "clock": "45",
            "red_cards": {"home": 0, "away": 1},
            "yellow_cards": {"home": 1, "away": 2},
            "corners": {"home": 4, "away": 3},
            "shots_on_target": {"home": 7, "away": 5},
            "substitutions": [],
            "var_events": [],
            "penalties": [],
            "free_kicks": [],
            "events": [
                {
                    "type": "yellow_card",
                    "team": "home",
                    "player": "Rice",
                    "minute": "12",
                    "result": None,
                },
                {
                    "type": "goal",
                    "team": "home",
                    "player": None,
                    "minute": "31",
                    "result": "1-0",
                },
            ],
            "lineups": {"home": ["Saka"], "away": ["Palmer"]},
            "commentary_available": "1193",
            "venue": "Emirates Stadium",
            "odds": {},
            "raw": GOALSERVE_JSON_FEED["scores"]["category"]["matches"]["match"],
        }
    ]


@pytest.mark.asyncio
async def test_goalserve_http_client_requests_json_feed_without_leaking_key():
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json=GOALSERVE_JSON_FEED)

    client = GoalserveHttpClient(
        api_key="SECRET_GOALSERVE_KEY",
        transport=httpx.MockTransport(handler),
    )

    rows = await client.fetch_home()
    await client.close()

    assert rows[0]["score"] == {"home": 2, "away": 1}
    assert "json=1" in requested_urls[0]
    assert "SECRET_GOALSERVE_KEY" not in str(rows)


@pytest.mark.asyncio
async def test_goalserve_ws_update_normalizes_stat_a_and_listener_persists_fields():
    store = MemoryStore()
    collector = Collector(
        store=store,
        pm_client=StaticPMHttpClient([PM_EVENTS[0]]),
        gs_client=GoalserveFixtureClient(),
    )
    collector.set_external_source("gs")
    report = await collector.collect_once()
    guid = report["bindings"][0]["guid"]
    listener = Listener(store=store, broadcaster=BroadcastHub())

    raw_update = {
        "mt": "updt",
        "sp": "soccer",
        "id": "live-100",
        "t1": {"n": "Arsenal"},
        "t2": {"n": "Chelsea"},
        "stat": {"a": "3-2", "yc": "2-3", "rc": "1-0", "corners": "8-6"},
        "events": {"event": {"type": "Penalty", "minute": "51", "team": "t1"}},
    }

    normalized = normalize_goalserve_ws_payload(raw_update)
    event = await listener.process_payload("gs_live", raw_update)
    gs_match = await store.get_json(f"gs:match:{guid}")

    assert normalized["score"] == {"home": 3, "away": 2}
    assert event is not None
    assert event["source"] == "gs_live"
    assert gs_match["score_home"] == 3
    assert gs_match["score_away"] == 2
    assert gs_match["yellow_cards"] == {"home": 2, "away": 3}
    assert gs_match["red_cards"] == {"home": 1, "away": 0}
    assert gs_match["corners"] == {"home": 8, "away": 6}
    assert gs_match["penalties"] == [
        {"type": "penalty", "team": "home", "player": None, "minute": "51", "result": None}
    ]


class GoalserveFixtureClient:
    async def fetch_home(self):
        return parse_goalserve_feed(GOALSERVE_JSON_FEED)

    async def fetch_d1(self):
        return []
