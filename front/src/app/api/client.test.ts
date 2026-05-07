import { afterEach, describe, expect, it, vi } from "vitest";
import {
  checkAuthSession,
  bindExternalMatch,
  fetchCollectorStatus,
  fetchExternalMatchCandidates,
  fetchHistoryMatches,
  fetchMatches,
  fetchLogs,
  fetchMatchSnapshots,
  fetchPmAccounts,
  fetchTrades,
  loginWithPassword,
  saveCollectorSettings,
  updateTradingInstance,
} from "./client";


function mockJsonResponse(payload: unknown) {
  return {
    ok: true,
    json: async () => payload,
  } as Response;
}


afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});


describe("auth client", () => {
  it("logs in with credentials included so the HttpOnly cookie is stored", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ authenticated: true, rotation_days: 3 }));
    vi.stubGlobal("fetch", fetchMock);

    await loginWithPassword("pw-0501");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/login",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: "pw-0501" }),
      })
    );
  });

  it("checks the session and includes cookies on protected API requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ authenticated: false, rotation_days: 3 }));
    vi.stubGlobal("fetch", fetchMock);

    await checkAuthSession();
    await fetchMatches();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/auth/session");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "include" });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/matches");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ credentials: "include" });
  });
});


describe("fetchTrades", () => {
  it("passes match_id to the backend when match-scoped trades are requested", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await fetchTrades({ matchId: "match-a", limit: 50 });

    const calledUrl = new URL(fetchMock.mock.calls[0][0] as string, "http://127.0.0.1:8088");
    expect(calledUrl.pathname).toBe("/api/v1/trades");
    expect(calledUrl.searchParams.get("match_id")).toBe("match-a");
    expect(calledUrl.searchParams.get("limit")).toBe("50");
  });
});


describe("fetchLogs", () => {
  it("passes match_id and limit to the backend when current-match logs are requested", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await fetchLogs({ matchId: "match-a", limit: 20 });

    const calledUrl = new URL(fetchMock.mock.calls[0][0] as string, "http://127.0.0.1:8088");
    expect(calledUrl.pathname).toBe("/api/v1/logs");
    expect(calledUrl.searchParams.get("match_id")).toBe("match-a");
    expect(calledUrl.searchParams.get("limit")).toBe("20");
  });
});


describe("updateTradingInstance", () => {
  it("sends a PUT payload for partial strategy updates", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ trading_id: "S001", status: "running", mode: "simulation", strategy_name: "football_score_delay_trade" }));
    vi.stubGlobal("fetch", fetchMock);

    await updateTradingInstance("S001", {
      strategy_params: { max_drawdown: 0.07, trade_amount: 150 },
      affect_sports: ["football"],
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/tradings/S001");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: "PUT",
      headers: { "Content-Type": "application/json" },
    });
    expect(fetchMock.mock.calls[0][1]?.body).toBe(
      JSON.stringify({
        strategy_params: { max_drawdown: 0.07, trade_amount: 150 },
        affect_sports: ["football"],
      })
    );
  });
});


describe("fetchMatchSnapshots", () => {
  it("requests the match snapshot history for chart rendering", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await fetchMatchSnapshots("258884", "live", 150);

    const calledUrl = new URL(fetchMock.mock.calls[0][0] as string, "http://127.0.0.1:8088");
    expect(calledUrl.pathname).toBe("/api/v1/matches/258884/snapshots");
    expect(calledUrl.searchParams.get("series")).toBe("live");
    expect(calledUrl.searchParams.get("limit")).toBe("150");
  });
});


describe("fetchCollectorStatus", () => {
  it("requests the live collector connection status", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        external_stream_enabled: true,
        external_stream_started: true,
        polymarket_ws_enabled: true,
        goalserve_ws_enabled: false,
        polymarket_ws_connected: true,
        goalserve_connected: true,
        matches_count: 5,
        last_tick_source: "polymarket",
        latest_tick_ts_utc: "2026-03-16T10:00:00Z",
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchCollectorStatus();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/collector/status");
  });
});

describe("fetchPmAccounts", () => {
  it("requests configured PM account summaries without exposing credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse([
        {
          id: "pm-main",
          name: "Main PM",
          chain_id: 137,
          funder_configured: true,
          private_key_configured: true,
          api_credentials_configured: true,
          live_trading_enabled: false,
          total_funds: 0,
          position_funds: 0,
          available_funds: 0,
        },
      ])
    );
    vi.stubGlobal("fetch", fetchMock);

    const rows = await fetchPmAccounts();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/pm/accounts");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "include" });
    expect(rows[0].id).toBe("pm-main");
  });
});

describe("saveCollectorSettings", () => {
  it("sends selected external source to the backend", async () => {
    const payload = {
      collection_interval_minutes: 3,
      football_volume_threshold_k: 600,
      external_source: "gs" as const,
    };
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);

    await saveCollectorSettings(payload);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/settings/collector");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  });
});

describe("fetchHistoryMatches", () => {
  it("requests the finished match list for the history sidebar view with pagination params", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await fetchHistoryMatches(120, 50);

    const calledUrl = new URL(fetchMock.mock.calls[0][0] as string, "http://127.0.0.1:8088");
    expect(calledUrl.pathname).toBe("/api/v1/matches/history");
    expect(calledUrl.searchParams.get("limit")).toBe("120");
    expect(calledUrl.searchParams.get("offset")).toBe("50");
  });
});

describe("manual external match binding", () => {
  it("requests sorted candidates and posts the selected external match", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await fetchExternalMatchCandidates("guid-1", "ggs", 25);
    await bindExternalMatch("guid-1", "ggs", "1608714");

    const candidateUrl = new URL(fetchMock.mock.calls[0][0] as string, "http://127.0.0.1:8088");
    expect(candidateUrl.pathname).toBe("/api/v1/matches/guid-1/external-candidates");
    expect(candidateUrl.searchParams.get("source")).toBe("ggs");
    expect(candidateUrl.searchParams.get("limit")).toBe("25");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/matches/guid-1/external-bind");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "ggs", external_match_id: "1608714" }),
    });
  });
});
