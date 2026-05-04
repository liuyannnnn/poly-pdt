PM_EVENTS = [
    {
        "id": "pm-ars-che",
        "slug": "epl-arsenal-chelsea-2026-05-01",
        "sport": "football",
        "league": "Premier League",
        "start_time": "2026-05-01T19:00:00Z",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "status": "scheduled",
        "score": {"home": 0, "away": 0},
        "game_id": 9001,
        "market": {
            "condition_id": "cond-ars-che",
            "tokens": {
                "home": "asset-ars-home",
                "draw": "asset-ars-draw",
                "away": "asset-ars-away",
            },
            "prices": {
                "home": {"bid": 0.43, "ask": 0.44},
                "draw": {"bid": 0.25, "ask": 0.27},
                "away": {"bid": 0.31, "ask": 0.33},
            },
        },
        "volume": {"moneyline": 120000, "total": 300000},
    },
    {
        "id": "pm-mun-liv",
        "slug": "epl-manchester-united-liverpool-2026-05-01",
        "sport": "football",
        "league": "Premier League",
        "start_time": "2026-05-01T21:00:00Z",
        "home_team": "Manchester Utd",
        "away_team": "Liverpool",
        "status": "scheduled",
        "score": {"home": 0, "away": 0},
        "market": {
            "condition_id": "cond-mun-liv",
            "tokens": {
                "home": "asset-mun-home",
                "draw": "asset-mun-draw",
                "away": "asset-mun-away",
            },
            "prices": {
                "home": {"bid": 0.36, "ask": 0.38},
                "draw": {"bid": 0.26, "ask": 0.28},
                "away": {"bid": 0.34, "ask": 0.36},
            },
        },
        "volume": {"moneyline": 90000, "total": 180000},
    },
    {
        "id": "pm-pending",
        "slug": "epl-everton-wolves-2026-05-01",
        "sport": "football",
        "league": "Premier League",
        "start_time": "2026-05-01T22:00:00Z",
        "home_team": "Everton",
        "away_team": "Wolves",
        "status": "scheduled",
        "score": {"home": 0, "away": 0},
        "market": {
            "condition_id": "cond-pending",
            "tokens": {
                "home": "asset-pending-home",
                "draw": "asset-pending-draw",
                "away": "asset-pending-away",
            },
            "prices": {
                "home": {"bid": 0.45, "ask": 0.47},
                "draw": {"bid": 0.24, "ask": 0.26},
                "away": {"bid": 0.29, "ask": 0.31},
            },
        },
        "volume": {"moneyline": 70000, "total": 120000},
    },
]

GS_HOME = [
    {
        "match_id": "gs-100",
        "pregame_id": "pre-100",
        "inplay_id": "live-100",
        "pm_game_id": 9001,
        "league": "Premier League",
        "start_time": "2026-05-01T19:00:00Z",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "status": "scheduled",
        "score": {"home": 0, "away": 0},
        "clock": "",
        "odds": {"home": 2.1, "draw": 3.2, "away": 3.4},
    }
]

GS_D1 = [
    {
        "match_id": "gs-200",
        "pregame_id": "pre-200",
        "inplay_id": "live-200",
        "league": "Premier League",
        "start_time": "2026-05-01T21:04:00Z",
        "home_team": "Manchester United",
        "away_team": "Liverpool FC",
        "status": "scheduled",
        "score": {"home": 0, "away": 0},
        "clock": "",
        "odds": {"home": 2.8, "draw": 3.3, "away": 2.6},
    }
]
