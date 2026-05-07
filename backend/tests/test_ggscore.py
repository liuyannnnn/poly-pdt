from app.ggscore import (
    GGScoreHttpPollSource,
    best_ggscore_match_for_pm,
    normalize_ggscore_match,
    normalize_ggscore_match_info,
    normalize_ggscore_ws_payload,
)


def test_ggscore_http_poll_source_has_distinct_runtime_status_name():
    assert GGScoreHttpPollSource.name == "ggs_http_poll"


def test_ggscore_result_normalizer_extracts_scores_and_provider_time():
    normalized = normalize_ggscore_match(
        {
            "sport_id": 202,
            "match_id": 3091184,
            "teams": [
                {"team_id": 64226, "side": 1, "team_name": "Bayern Munich", "score": 1},
                {"team_id": 23986, "side": 2, "team_name": "PSG", "score": 0},
            ],
            "update_time": 0,
        }
    )

    assert normalized["source"] == "ggs"
    assert normalized["match_id"] == "3091184"
    assert normalized["score_home"] == 1
    assert normalized["score_away"] == 0
    assert normalized["score"] == {"home": 1, "away": 0}
    assert normalized["home_team"] == "Bayern Munich"
    assert normalized["away_team"] == "PSG"
    assert normalized["home_team_id"] == "64226"
    assert normalized["away_team_id"] == "23986"
    assert normalized["provider_ts_utc"] == "1970-01-01T00:00:00Z"


def test_ggscore_ws_normalizer_unwraps_push_payload():
    rows = normalize_ggscore_ws_payload(
        {
            "channel": "result",
            "push_type": "update",
            "push_time_millis": 1000,
            "data": {
                "match_id": 3091184,
                "teams": [
                    {"side": 1, "team_id": 64226, "score": 2},
                    {"side": 2, "team_id": 23986, "score": 1},
                ],
            },
        }
    )

    assert rows == [
        {
            "source": "ggs",
            "match_id": "3091184",
            "inplay_id": "3091184",
            "score": {"home": 2, "away": 1},
            "score_home": 2,
            "score_away": 1,
            "home_team": "64226",
            "away_team": "23986",
            "home_team_id": "64226",
            "away_team_id": "23986",
            "status": None,
            "match_time": "",
            "period": "",
            "clock": "",
            "provider_ts_utc": "1970-01-01T00:00:01Z",
            "ts": "1970-01-01T00:00:01Z",
            "raw": {
                "match_id": 3091184,
                "teams": [
                    {"side": 1, "team_id": 64226, "score": 2},
                    {"side": 2, "team_id": 23986, "score": 1},
                ],
            },
        }
    ]


def test_ggscore_match_list_normalizer_extracts_team_names():
    normalized = normalize_ggscore_match_info(
        {
            "sport_id": 202,
            "match_id": 5235703,
            "league": {"name_en": "UEFA Champions League"},
            "start_time": 1778094000,
            "status": "upcoming",
            "teams": [
                {
                    "side": 1,
                    "score": [0, 0, 0],
                    "team_info": {"team_id": 66, "name_en": "FC Bayern Munich", "abbr_en": "Bayern Munich"},
                },
                {
                    "side": 2,
                    "score": [0, 0, 0],
                    "team_info": {"team_id": 179, "name_en": "Paris Saint Germain", "abbr_en": "PSG"},
                },
            ],
            "update_time": 1778065913,
        }
    )

    assert normalized["source"] == "ggs"
    assert normalized["match_id"] == "5235703"
    assert normalized["external_match_id"] == "5235703"
    assert normalized["home_team"] == "FC Bayern Munich"
    assert normalized["away_team"] == "Paris Saint Germain"
    assert normalized["home_team_id"] == "66"
    assert normalized["away_team_id"] == "179"
    assert normalized["score_home"] == 0
    assert normalized["score_away"] == 0
    assert normalized["start_time_utc"] == "2026-05-06T19:00:00Z"


def test_ggscore_best_match_finds_pm_equivalent_teams():
    candidate = best_ggscore_match_for_pm(
        {
            "home_team": "Fc Bayern München",
            "away_team": "Paris Saint Germain Fc",
            "start_time_utc": "2026-05-06T19:00:00Z",
        },
        [
            {
                "match_id": "5235703",
                "home_team": "FC Bayern Munich",
                "away_team": "Paris Saint Germain",
                "start_time_utc": "2026-05-06T19:00:00Z",
            },
        ],
    )

    assert candidate is not None
    assert candidate["match_id"] == "5235703"
    assert candidate["team_confidence"] > 0.9
