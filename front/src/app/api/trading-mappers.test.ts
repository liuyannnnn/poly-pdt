import { describe, expect, it } from "vitest";
import {
  applyMatchLogToMatches,
  applyTraderEventToMatches,
  applyExternalUpdateToMatches,
  applyWsTicksToMatches,
  applyWsTickToMatches,
  formatCompactUsdVolume,
  groupMatchesByStatus,
  reconcileSidebarMatchOrder,
  resolveSelectedMatchId,
  mergeMatchesPreservingRealtime,
  mapBackendAccountToTradingAccount,
  mapBackendPositionRow,
  mapBackendTradeRow,
  passesCollectorThreshold,
  profitColorClass,
  formatSignedUsd,
  resolveTraderInitialBalance,
  displayTradingId,
  sortMatchesForSidebar,
} from "./trading-mappers";


describe("groupMatchesByStatus", () => {
  it("groups matches into live, pre and finished buckets", () => {
    const rows = [
      { id: "m1", status: "Live" },
      { id: "m2", status: "Scheduled" },
      { id: "m3", status: "Live" },
      { id: "m4", status: "Finished" },
    ] as any[];
    const grouped = groupMatchesByStatus(rows);
    expect(grouped.live.map((m) => m.id)).toEqual(["m1", "m3"]);
    expect(grouped.pre.map((m) => m.id)).toEqual(["m2"]);
    expect(grouped.finished.map((m) => m.id)).toEqual(["m4"]);
  });

  it("excludes finished matches from the main pre-match bucket", () => {
    const rows = [
      { id: "m1", status: "Finished" },
      { id: "m2", status: "Scheduled" },
    ] as any[];
    const grouped = groupMatchesByStatus(rows);
    expect(grouped.live).toEqual([]);
    expect(grouped.pre.map((m) => m.id)).toEqual(["m2"]);
    expect(grouped.finished.map((m) => m.id)).toEqual(["m1"]);
  });
});

describe("sortMatchesForSidebar", () => {
  it("orders sidebar cards by start time descending within each status bucket", () => {
    const rows = [
      { id: "scheduled-late", status: "Scheduled", startTime: new Date("2026-04-02T10:00:00Z") },
      { id: "live-earlier", status: "Live", wsTime: new Date("2026-04-01T10:00:00Z"), startTime: new Date("2026-04-01T09:00:00Z") },
      { id: "scheduled-near", status: "Scheduled", startTime: new Date("2026-04-01T12:00:00Z") },
      { id: "live-latest", status: "Live", wsTime: new Date("2026-04-01T11:00:00Z"), startTime: new Date("2026-04-01T10:00:00Z") },
      { id: "finished", status: "Finished", startTime: new Date("2026-03-31T10:00:00Z") },
    ] as any[];

    const sorted = sortMatchesForSidebar(rows);
    expect(sorted.map((m) => m.id)).toEqual([
      "live-latest",
      "live-earlier",
      "scheduled-late",
      "scheduled-near",
      "finished",
    ]);
  });
});

describe("reconcileSidebarMatchOrder", () => {
  it("re-sorts incoming cards by start time descending", () => {
    const previous = [
      { id: "live-1", status: "Live", startTime: new Date("2026-04-02T10:00:00Z") },
      { id: "live-2", status: "Live", startTime: new Date("2026-04-02T11:00:00Z") },
      { id: "pre-1", status: "Scheduled", startTime: new Date("2026-04-02T12:00:00Z") },
    ] as any[];
    const incoming = [
      { id: "live-2", status: "Live", startTime: new Date("2026-04-02T11:00:00Z") },
      { id: "pre-1", status: "Scheduled", startTime: new Date("2026-04-02T12:00:00Z") },
      { id: "live-1", status: "Live", startTime: new Date("2026-04-02T10:00:00Z") },
    ] as any[];

    const reconciled = reconcileSidebarMatchOrder(previous, incoming);
    expect(reconciled.map((row) => row.id)).toEqual(["live-2", "live-1", "pre-1"]);
  });

  it("moves a pre-match to the front when it turns live", () => {
    const previous = [
      { id: "live-1", status: "Live", startTime: new Date("2026-04-02T10:00:00Z") },
      { id: "pre-1", status: "Scheduled", startTime: new Date("2026-04-02T12:00:00Z") },
      { id: "pre-2", status: "Scheduled", startTime: new Date("2026-04-02T13:00:00Z") },
    ] as any[];
    const incoming = [
      { id: "live-1", status: "Live", startTime: new Date("2026-04-02T10:00:00Z") },
      { id: "pre-1", status: "Live", startTime: new Date("2026-04-02T12:00:00Z") },
      { id: "pre-2", status: "Scheduled", startTime: new Date("2026-04-02T13:00:00Z") },
    ] as any[];

    const reconciled = reconcileSidebarMatchOrder(previous, incoming);
    expect(reconciled.map((row) => row.id)).toEqual(["pre-1", "live-1", "pre-2"]);
  });
});

describe("resolveSelectedMatchId", () => {
  it("keeps the current selection even when polled rows arrive in a different order", () => {
    const rows = [
      { id: "match-1", status: "Live" },
      { id: "match-2", status: "Live" },
      { id: "match-3", status: "Scheduled" },
    ] as any[];

    expect(resolveSelectedMatchId("match-2", rows)).toBe("match-2");
  });

  it("falls back to the first row only when the current selection no longer exists", () => {
    const rows = [
      { id: "match-1", status: "Live" },
      { id: "match-3", status: "Scheduled" },
    ] as any[];

    expect(resolveSelectedMatchId("match-2", rows)).toBe("match-1");
    expect(resolveSelectedMatchId(null, rows)).toBe("match-1");
    expect(resolveSelectedMatchId("match-2", [])).toBeNull();
  });

  it("keeps a selected history match while current match polling refreshes", () => {
    const currentRows = [
      { id: "live-1", status: "Live" },
      { id: "pre-1", status: "Scheduled" },
    ] as any[];
    const historyRows = [
      { id: "finished-1", status: "Finished" },
    ] as any[];

    expect(resolveSelectedMatchId("finished-1", currentRows, historyRows)).toBe("finished-1");
  });
});

describe("mergeMatchesPreservingRealtime", () => {
  it("keeps newer websocket quotes and live clock when polled backend rows are older", () => {
    const previous = [
      {
        id: "EVT-001",
        status: "Live",
        startTime: new Date("2026-04-03T00:00:00Z"),
        wsTime: new Date("2026-04-03T00:10:00Z"),
        period: "Q3",
        clock: "05:57",
        scoreA: 89,
        scoreB: 63,
        marketA: { bid: 0.71, ask: 0.73 },
        marketB: { bid: 0.27, ask: 0.29 },
      },
    ] as any[];

    const incoming = [
      {
        id: "EVT-001",
        status: "Live",
        startTime: new Date("2026-04-03T00:00:00Z"),
        wsTime: new Date("2026-04-03T00:05:00Z"),
        period: "Live",
        clock: "--:--",
        scoreA: 88,
        scoreB: 62,
        marketA: { bid: 0.4, ask: 0.42 },
        marketB: { bid: 0.58, ask: 0.6 },
      },
    ] as any[];

    const merged = mergeMatchesPreservingRealtime(previous, incoming);
    expect(merged[0].period).toBe("Q3");
    expect(merged[0].clock).toBe("05:57");
    expect(merged[0].marketA.ask).toBe(0.73);
    expect(merged[0].marketB.bid).toBe(0.27);
    expect(merged[0].scoreA).toBe(89);
    expect(merged[0].scoreB).toBe(63);
  });
});

describe("applyWsTicksToMatches", () => {
  it("uses the latest real tick per outcome when high-frequency ticks are flushed together", () => {
    const rows = [
      {
        id: "match-1",
        status: "Live",
        scoreA: 0,
        scoreB: 0,
        period: "Live",
        clock: "1H 10",
        wsTime: new Date("2026-05-03T16:00:00Z"),
        marketA: { bid: 0.2, ask: 0.21 },
        marketB: { bid: 0.3, ask: 0.31 },
        marketDraw: { bid: 0.4, ask: 0.41 },
      },
    ] as any[];

    const updated = applyWsTicksToMatches(rows, [
      {
        match_id: "match-1",
        outcome: "home",
        bid: 0.22,
        ask: 0.23,
        ts_utc: "2026-05-03T16:00:10.004000Z",
      },
      {
        match_id: "match-1",
        outcome: "home",
        bid: 0.24,
        ask: 0.25,
        ts_utc: "2026-05-03T16:00:10.900000Z",
      },
      {
        match_id: "match-1",
        outcome: "away",
        bid: 0.35,
        ask: 0.36,
        ts_utc: "2026-05-03T16:00:09.500000Z",
      },
    ] as any[]);

    expect(updated[0].marketA).toEqual({ bid: 0.24, ask: 0.25 });
    expect(updated[0].marketB).toEqual({ bid: 0.35, ask: 0.36 });
    expect(updated[0].wsTime.toISOString()).toBe("2026-05-03T16:00:10.900Z");
  });
});

describe("formatCompactUsdVolume", () => {
  it("formats thousands in K and millions in M with two decimals", () => {
    expect(formatCompactUsdVolume(999_500)).toBe("$999.50K");
    expect(formatCompactUsdVolume(1_250_000)).toBe("$1.25M");
  });
});

describe("trading detail row mappers", () => {
  it("maps persisted positions with slug, team display and compact order id", () => {
    const mapped = mapBackendPositionRow({
      trading_id: "S005",
      order_id: "1234567890abcdef-extra",
      guid: "guid-1",
      slug: "sea-sas-mil-2026-05-03",
      outcome_key: "home",
      team_label: "Home",
      team_name: "Us Sassuolo Calcio",
      shares: 100,
      avg_entry_price: 0.2,
      current_bid1: 0.19,
      cost_basis: 20,
      unrealized_pnl: -1,
    });

    expect(mapped.orderId).toBe("1234567890abcdef");
    expect(mapped.matchName).toBe("sea-sas-mil-2026-05-03");
    expect(mapped.slug).toBe("sea-sas-mil-2026-05-03");
    expect(mapped.teamDisplayName).toBe("Home Us Sassuolo Calcio");
    expect(mapped.currentPrice).toBe(0.19);
    expect(mapped.profitPercent).toBe(-5);
  });

  it("maps persisted trades with slug and reason and hides buy profit", () => {
    const buy = mapBackendTradeRow({
      trading_id: "S005",
      order_id: "abcdef1234567890ZZZ",
      guid: "guid-1",
      slug: "sea-sas-mil-2026-05-03",
      outcome_key: "away",
      team_label: "Away",
      team_name: "Ac Milan",
      side: "buy",
      price: 0.2,
      amount_usd: 20,
      shares: 100,
      reason: "进球买入",
      ts_utc: "2026-05-03T13:08:09Z",
    });
    const sell = mapBackendTradeRow({
      trading_id: "S005",
      order_id: "fedcba0987654321",
      guid: "guid-1",
      slug: "sea-sas-mil-2026-05-03",
      outcome_key: "home",
      team_label: "Home",
      team_name: "Us Sassuolo Calcio",
      side: "sell",
      price: 0.19,
      cost_basis: 20,
      amount_usd: 19,
      profit: -1,
      profit_rate: -5,
      shares: 100,
      reason: "回撤0.05卖出",
      ts_utc: "2026-05-03T13:08:51Z",
    });

    expect(buy.orderId).toBe("abcdef1234567890");
    expect(buy.slug).toBe("sea-sas-mil-2026-05-03");
    expect(buy.side).toBe("Away Ac Milan / B");
    expect(buy.reason).toBe("进球买入");
    expect(buy.profit).toBeNull();
    expect(sell.entryPrice).toBe(0.2);
    expect(sell.exitPrice).toBe(0.19);
    expect(sell.reason).toBe("回撤0.05卖出");
  });

  it("creates a stable 16 character display order id when old simulation rows lack one", () => {
    const mapped = mapBackendTradeRow({
      trading_id: "S005",
      guid: "guid-397282",
      side: "buy",
      outcome_key: "home",
      price: 0.2,
      shares: 100,
      ts_utc: "2026-05-03T13:08:09Z",
    });

    expect(mapped.orderId).toHaveLength(16);
    expect(mapBackendTradeRow({
      trading_id: "S005",
      guid: "guid-397282",
      side: "buy",
      outcome_key: "home",
      price: 0.2,
      shares: 100,
      ts_utc: "2026-05-03T13:08:09Z",
    }).orderId).toBe(mapped.orderId);
  });
});


describe("mapBackendAccountToTradingAccount", () => {
  it("maps backend account payload to panel account schema", () => {
    const row = {
      id: "S001",
      mode: "simulation",
      strategy_name: "football_score_delay_trade",
      retracement: 0.05,
      initial_balance: 1000.0,
      affect_sports: ["football"],
      strategy_params: {
        initial_balance: 1000.0,
        max_drawdown: 0.05,
        trade_amount: 150.0,
      },
      total_assets: 1200.0,
      available_cash: 800.0,
      today_profit: 25.5,
      position_count: 2,
      is_running: true,
      win_rate: 0.5,
    };
    const mapped = mapBackendAccountToTradingAccount(row as any);
    expect(mapped.id).toBe("S001");
    expect(mapped.totalAssets).toBe(1200.0);
    expect(mapped.availableCash).toBe(800.0);
    expect(mapped.todayProfit).toBe(25.5);
    expect(mapped.positionCount).toBe(2);
    expect(mapped.isRunning).toBe(true);
    expect(mapped.initialBalance).toBe(1000.0);
    expect(mapped.sports).toEqual(["足球"]);
    expect(mapped.winRate).toBe(50);
    expect(mapped.strategyName).toBe("足球-比分时差交易");
    expect(mapped.strategyKey).toBe("football_score_delay_trade");
    expect(mapped.maxSingleOrderPct).toBe(20);
    expect(mapped.strategyConfig.trade_amount).toBe(150);
  });

  it("maps common risk params from the backend payload", () => {
    const row = {
      id: "S002",
      mode: "simulation",
      strategy_name: "football_score_delay_trade",
      retracement: 0.05,
      initial_balance: 2000.0,
      affect_sports: ["football"],
      strategy_params: {
        initial_balance: 2000.0,
        max_drawdown_pct: 5.0,
        risk: {
          max_positions: 3,
          max_fund_usage_pct: 60,
          max_single_order_pct: 20,
          max_add_count: 2,
          max_add_fund_pct: 10,
          stop_loss_drawdown: 0.05,
        },
      },
      total_assets: 2100.0,
      available_cash: 1800.0,
      position_count: 1,
      is_running: false,
      win_rate: 0.25,
    };
    const mapped = mapBackendAccountToTradingAccount(row as any);
    expect(mapped.strategyName).toBe("足球-比分时差交易");
    expect(mapped.maxPositions).toBe(3);
    expect(mapped.maxFundUsageRate).toBe(60);
    expect(mapped.maxSingleOrderPct).toBe(20);
    expect(mapped.maxAddCount).toBe(2);
    expect(mapped.maxAddFundPct).toBe(10);
    expect(mapped.stopLossDrawdown).toBe(0.05);
    expect(mapped.strategyParams.retracement).toBe(5);
  });

  it("maps the winrate gap strategy label", () => {
    const mapped = mapBackendAccountToTradingAccount({
      id: "S003",
      mode: "simulation",
      strategy_name: "football_winrate_gap_buy",
      retracement: 0.05,
      initial_balance: 1000,
      affect_sports: ["football"],
      strategy_params: {},
      total_assets: 1000,
      available_cash: 1000,
      position_count: 0,
      is_running: false,
      win_rate: 0,
    } as any);

    expect(mapped.strategyName).toBe("胜率差买入");
  });
});


describe("resolveTraderInitialBalance", () => {
  it("uses configured PM funds for real traders instead of the simulation default", () => {
    expect(resolveTraderInitialBalance("real", "10000", { total_funds: 0, available_funds: 0 })).toBe(0);
    expect(resolveTraderInitialBalance("real", "10000", { total_funds: 125.5, available_funds: 80 })).toBe(125.5);
  });

  it("keeps the form value fallback for simulation traders", () => {
    expect(resolveTraderInitialBalance("simulation", "", undefined)).toBe(10000);
    expect(resolveTraderInitialBalance("simulation", "2500", undefined)).toBe(2500);
  });
});


describe("applyWsTickToMatches", () => {
  it("updates the home market only when the tick explicitly names the home outcome", () => {
    const now = Date.now();
    const source = [
      {
        id: "pm_football_001",
        marketA: { bid: 0.5, ask: 0.52 },
        marketB: { bid: 0.48, ask: 0.5 },
        wsTime: new Date(now - 1000),
      },
    ] as any[];
    const updated = applyWsTickToMatches(source, {
      match_id: "pm_football_001",
      outcome: "home",
      bid: 0.61,
      ask: 0.63,
      ts_utc: new Date(now).toISOString(),
    });
    expect(updated[0].marketA.bid).toBe(0.61);
    expect(updated[0].marketA.ask).toBe(0.63);
    expect(updated[0].marketB.bid).toBe(0.48);
    expect(updated[0].marketB.ask).toBe(0.5);
  });

  it("updates the matching outcome instead of always overwriting marketA", () => {
    const now = Date.now();
    const source = [
      {
        id: "pm_football_001",
        marketA: { bid: 0.43, ask: 0.44 },
        marketB: { bid: 0.56, ask: 0.57 },
        marketDraw: { bid: 0.12, ask: 0.13 },
        wsTime: new Date(now - 1000),
      },
    ] as any[];
    const updated = applyWsTickToMatches(source, {
      match_id: "pm_football_001",
      outcome: "away",
      bid: 0.58,
      ask: 0.59,
      ts_utc: new Date(now).toISOString(),
    });
    expect(updated[0].marketA.bid).toBe(0.43);
    expect(updated[0].marketB.bid).toBe(0.58);
    expect(updated[0].marketB.ask).toBe(0.59);
    expect(updated[0].marketDraw?.bid).toBe(0.12);
  });

  it("updates real live status, score and match time carried by market ticks", () => {
    const source = [
      {
        id: "pm_football_001",
        scoreA: 0,
        scoreB: 0,
        status: "Scheduled",
        period: "",
        clock: "",
        marketA: { bid: 0.57, ask: 0.58 },
        marketB: { bid: 0.18, ask: 0.19 },
        marketDraw: { bid: 0.23, ask: 0.24 },
        wsTime: new Date("2026-05-02T14:00:00Z"),
      },
    ] as any[];

    const updated = applyWsTickToMatches(source, {
      match_id: "pm_football_001",
      outcome: "home",
      bid: 0.59,
      ask: 0.6,
      ts_utc: "2026-05-02T14:38:53Z",
      status: "InProgress",
      match_time: "2H 49",
      score_home: 1,
      score_away: 2,
    });

    expect(updated[0].status).toBe("Live");
    expect(updated[0].period).toBe("2H 49");
    expect(updated[0].scoreA).toBe(1);
    expect(updated[0].scoreB).toBe(2);
    expect(updated[0].marketA.bid).toBe(0.59);
  });

  it("ignores ticks without an explicit outcome instead of deriving another side", () => {
    const now = Date.now();
    const source = [
      {
        id: "pm_football_001",
        marketA: { bid: 0.75, ask: 0.76 },
        marketB: { bid: 0.06, ask: 0.07 },
        marketDraw: { bid: 0.18, ask: 0.19 },
        wsTime: new Date(now - 1000),
      },
    ] as any[];

    const updated = applyWsTickToMatches(source, {
      match_id: "pm_football_001",
      bid: 0.75,
      ask: 0.76,
      ts_utc: new Date(now).toISOString(),
    });

    expect(updated[0]).toEqual(source[0]);
  });
});

describe("applyTraderEventToMatches", () => {
  it("updates match score, status and moneyline quotes from trader.event websocket payload", () => {
    const source = [
      {
        id: "EVT-001",
        scoreA: 0,
        scoreB: 0,
        status: "Scheduled",
        marketA: { bid: 0.5, ask: 0.52 },
        marketB: { bid: 0.48, ask: 0.5 },
        marketDraw: { bid: 0.1, ask: 0.11 },
        wsTime: new Date("2026-03-31T10:00:00Z"),
      },
    ] as any[];

    const updated = applyTraderEventToMatches(source, {
      topic: "trader.event",
      payload: {
        event_id: "EVT-001",
        source_ts_utc: "2026-03-31T10:05:00Z",
        pm: {
          status: "live",
          home_score: 1,
          away_score: 0,
          markets: [
            { market_type: "moneyline", outcome_key: "home", bid1: 0.61, ask1: 0.63 },
            { market_type: "moneyline", outcome_key: "away", bid1: 0.22, ask1: 0.24 },
            { market_type: "moneyline", outcome_key: "draw", bid1: 0.14, ask1: 0.16 },
          ],
        },
      },
    });

    expect(updated[0].scoreA).toBe(1);
    expect(updated[0].scoreB).toBe(0);
    expect(updated[0].status).toBe("Live");
    expect(updated[0].marketA.bid).toBe(0.61);
    expect(updated[0].marketB.ask).toBe(0.24);
    expect(updated[0].marketDraw.ask).toBe(0.16);
  });

  it("updates live match time from trader.event match_time payload", () => {
    const source = [
      {
        id: "EVT-002",
        scoreA: 0,
        scoreB: 0,
        status: "Live",
        period: "Live",
        clock: "--:--",
        marketA: { bid: 0.5, ask: 0.52 },
        marketB: { bid: 0.48, ask: 0.5 },
        wsTime: new Date("2026-03-31T10:00:00Z"),
      },
    ] as any[];

    const updated = applyTraderEventToMatches(source, {
      topic: "trader.event",
      payload: {
        event_id: "EVT-002",
        source_ts_utc: "2026-03-31T10:05:00Z",
        pm: {
          status: "live",
          match_time: "Q3 08:41",
          home_score: 89,
          away_score: 63,
          markets: [],
        },
      },
    });

    expect(updated[0].period).toBe("Q3");
    expect(updated[0].clock).toBe("08:41");
  });

  it("keeps halftime matches in the live bucket on websocket updates", () => {
    const rows = [
      {
        id: "EVT-001",
        status: "Live",
        startTime: new Date("2026-04-21T17:00:00Z"),
        wsTime: new Date("2026-04-21T17:45:00Z"),
        period: "1H",
        clock: "45:00",
        scoreA: 1,
        scoreB: 0,
        marketA: { bid: 0.7, ask: 0.71 },
        marketB: { bid: 0.1, ask: 0.11 },
        marketDraw: { bid: 0.19, ask: 0.2 },
      },
    ] as any[];

    const updated = applyTraderEventToMatches(rows, {
      topic: "trader.event",
      payload: {
        event_id: "EVT-001",
        source_ts_utc: "2026-04-21T17:46:00Z",
        pm: {
          status: "halftime",
          match_time: "HT",
          home_score: 1,
          away_score: 0,
        },
      },
    });

    expect(updated[0].status).toBe("Live");
    expect(updated[0].period).toBe("HT");
  });
});

describe("applyMatchLogToMatches", () => {
  it("uses match.log timestamps to refresh the sidebar time for external-only stat changes", () => {
    const rows = [
      {
        id: "guid-397942",
        scoreA: 2,
        scoreB: 2,
        status: "Live",
        wsTime: new Date("2026-05-03T15:53:58Z"),
      },
    ] as any[];

    const updated = applyMatchLogToMatches(rows, {
      topic: "match.log",
      payload: {
        guid: "guid-397942",
        ts_utc: "2026-05-03T15:54:41.432764Z",
        data_source: "asa_live",
        event_kind: "shots_on_target_changed",
      },
    });

    expect(updated[0].wsTime.toISOString()).toBe("2026-05-03T15:54:41.432Z");
    expect(updated[0].scoreA).toBe(2);
    expect(updated[0].scoreB).toBe(2);
  });
});

describe("applyExternalUpdateToMatches", () => {
  it("refreshes the sidebar timestamp from every external source push without changing PM score", () => {
    const rows = [
      {
        id: "guid-397942",
        scoreA: 2,
        scoreB: 2,
        status: "Live",
        wsTime: new Date("2026-05-03T15:53:58Z"),
      },
    ] as any[];

    const updated = applyExternalUpdateToMatches(rows, {
      topic: "external.match",
      payload: {
        guid: "guid-397942",
        score_home: 3,
        score_away: 2,
        updated_at_utc: "2026-05-03T15:54:41.432764Z",
      },
    });

    expect(updated[0].wsTime.toISOString()).toBe("2026-05-03T15:54:41.432Z");
    expect(updated[0].scoreA).toBe(2);
    expect(updated[0].scoreB).toBe(2);
  });
});


describe("passesCollectorThreshold", () => {
  it("uses total volume instead of moneyline volume", () => {
    expect(
      passesCollectorThreshold(
        {
          sport: "Football",
          moneylineVolume: 26.1764,
          totalVolume: 200872.740588,
        } as any,
        {
          footballVol: 200,
        }
      )
    ).toBe(true);
  });
});


describe("profitColorClass", () => {
  it("uses China-style red for profit, green for loss, and grey for flat", () => {
    expect(profitColorClass(1)).toBe("text-red-500");
    expect(profitColorClass(-1)).toBe("text-green-600");
    expect(profitColorClass(0)).toBe("text-gray-500");
  });
});


describe("formatSignedUsd", () => {
  it("keeps the minus sign for losses", () => {
    expect(formatSignedUsd(12.3)).toBe("+$12.30");
    expect(formatSignedUsd(-7.4)).toBe("-$7.40");
    expect(formatSignedUsd(0)).toBe("$0.00");
  });
});


describe("displayTradingId", () => {
  it("shows trader ids with S/R mode prefixes", () => {
    expect(displayTradingId("S004", "simulation")).toBe("S004");
    expect(displayTradingId("R003", "real")).toBe("R003");
    expect(displayTradingId("manual", "simulation")).toBe("S-manual");
    expect(displayTradingId("manual", "real")).toBe("R-manual");
  });
});
