from app.allsportsapi import normalize_allsportsapi_match, normalize_allsportsapi_ws_payload


def test_asa_normalizer_keeps_live_status_when_payload_has_match_clock():
    normalized = normalize_allsportsapi_match(
        {
            "event_key": "asa-1",
            "event_home_team": "Metz",
            "event_away_team": "Monaco",
            "status": "scheduled",
            "match_time": "84",
            "event_final_result": "1 - 1",
        }
    )

    assert normalized["status"] == "live"
    assert normalized["clock"] == "84"
    assert normalized["period"] == "2H"
    assert normalized["score_home"] == 1
    assert normalized["score_away"] == 1


def test_asa_ws_partial_payload_without_score_does_not_emit_fake_scheduled_status():
    normalized = normalize_allsportsapi_ws_payload(
        {
            "event_key": "asa-1",
            "statistics": [{"type": "Corners", "home": "8", "away": "3"}],
        }
    )

    assert normalized["status"] is None
    assert normalized["score_home"] is None
    assert normalized["score_away"] is None
    assert normalized["corners"] == {"home": 8, "away": 3}


def test_asa_ws_normalizer_is_idempotent_for_already_normalized_payload():
    payload = {
        "source": "asa",
        "match_id": "1608716",
        "home_team": "Osasuna",
        "away_team": "Barcelona",
        "start_time_utc": "2026-05-02T19:00:00Z",
        "score": {"home": 1, "away": 2},
        "score_home": 1,
        "score_away": 2,
        "corners": {"home": 8, "away": 3},
    }

    assert normalize_allsportsapi_ws_payload(payload) is payload
