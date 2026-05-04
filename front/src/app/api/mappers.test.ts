import { describe, expect, it } from "vitest";
import { mapBackendMatchToUiMatch } from "./mappers";


describe("mapBackendMatchToUiMatch", () => {
  it("maps backend football match to UI schema", () => {
    const row = {
      match_id: "pm_football_001",
      sport: "football",
      league: "Premier League",
      team_home: "Arsenal",
      team_away: "Chelsea",
      start_time_utc: "2026-03-15T10:00:00Z",
      status: "live",
      moneyline_volume: 120000,
      total_volume: 300000,
      latest_ts_utc: "2026-03-15T10:01:00Z",
      external_event_slug: "epl-ars-che-2026-03-15",
      pm_match_time: "Q2 05:01",
      score_home: 2,
      score_away: 1,
      game_id: 20023454,
      home_bid: 0.43,
      home_ask: 0.44,
      home_price: 0.435,
      away_bid: 0.56,
      away_ask: 0.57,
      away_price: 0.565,
      home_logo_url: "https://img.example/arsenal.png",
      away_logo_url: "https://img.example/chelsea.png",
    };
    const ui = mapBackendMatchToUiMatch(row);
    expect(ui.id).toBe("pm_football_001");
    expect(ui.slug).toBe("epl-ars-che-2026-03-15");
    expect(ui.sport).toBe("Football");
    expect(ui.teamA.name).toBe("Arsenal");
    expect(ui.teamB.name).toBe("Chelsea");
    expect(ui.teamA.logoUrl).toBe("https://img.example/arsenal.png");
    expect(ui.teamB.logoUrl).toBe("https://img.example/chelsea.png");
    expect(ui.status).toBe("Live");
    expect(ui.period).toBe("Q2");
    expect(ui.clock).toBe("05:01");
    expect(ui.scoreA).toBe(2);
    expect(ui.scoreB).toBe(1);
    expect(ui.gameId).toBe(20023454);
    expect(ui.marketA.bid).toBe(0.43);
    expect(ui.marketA.ask).toBe(0.44);
    expect(ui.marketB.bid).toBe(0.56);
    expect(ui.marketB.ask).toBe(0.57);
    expect(ui.homePrice).toBe(0.435);
    expect(ui.awayPrice).toBe(0.565);
    expect(ui.volume).toBe(120000);
    expect(ui.moneylineVolume).toBe(120000);
    expect(ui.totalVolume).toBe(300000);
  });

  it("normalizes live-like and ended style backend statuses for the sidebar", () => {
    const liveRow = {
      match_id: "pm_football_002",
      sport: "football",
      league: "Premier League",
      team_home: "Arsenal",
      team_away: "Chelsea",
      start_time_utc: "2026-04-01T23:00:00Z",
      status: "inplay",
      moneyline_volume: 100000,
      total_volume: 200000,
      latest_ts_utc: "2026-04-01T23:05:00Z",
    };
    const finishedRow = {
      ...liveRow,
      match_id: "pm_football_003",
      status: "closed",
    };
    const inProgressRow = {
      ...liveRow,
      match_id: "pm_football_004",
      status: "InProgress",
    };

    expect(mapBackendMatchToUiMatch(liveRow as any).status).toBe("Live");
    expect(mapBackendMatchToUiMatch(inProgressRow as any).status).toBe("Live");
    expect(mapBackendMatchToUiMatch(finishedRow as any).status).toBe("Finished");
  });

  it("does not derive a missing away quote from the home quote", () => {
    const ui = mapBackendMatchToUiMatch({
      match_id: "pm_football_005",
      sport: "football",
      league: "Premier League",
      team_home: "Arsenal",
      team_away: "Chelsea",
      start_time_utc: "2026-04-01T23:00:00Z",
      status: "live",
      moneyline_volume: 100000,
      total_volume: 200000,
      latest_ts_utc: "2026-04-01T23:05:00Z",
      home_bid: 0.75,
      home_ask: 0.76,
    } as any);

    expect(ui.marketB.bid).toBe(0);
    expect(ui.marketB.ask).toBe(0);
  });

  it("formats raw PM league payloads as a readable league label", () => {
    const ui = mapBackendMatchToUiMatch({
      match_id: "pm_football_004",
      sport: "football",
      league: "[{'id': '10364', 'title': 'Indian Super League', 'slug': 'indian-super-league'}]",
      team_home: "Odisha Fc",
      team_away: "Jamshedpur Fc",
      start_time_utc: "2026-05-01T19:00:00Z",
      status: "scheduled",
      moneyline_volume: 120000,
      total_volume: 600000,
      latest_ts_utc: "2026-05-01T10:00:00Z",
    });

    expect(ui.league).toBe("Indian Super League");
  });
});
