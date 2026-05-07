export interface BackendMatchCard {
  match_id: string;
  sport: "football";
  league: string;
  team_home: string;
  team_away: string;
  home_logo_url?: string | null;
  away_logo_url?: string | null;
  start_time_utc: string;
  status: string;
  moneyline_volume: number;
  total_volume: number;
  latest_ts_utc: string;
  score_home?: number | null;
  score_away?: number | null;
  pm_match_time?: string | null;
  external_event_id?: string;
  external_event_slug?: string;
  external_market_id?: string;
  external_market_slug?: string;
  game_id?: number | null;
  home_bid?: number | null;
  home_ask?: number | null;
  home_price?: number | null;
  away_bid?: number | null;
  away_ask?: number | null;
  away_price?: number | null;
  draw_bid?: number | null;
  draw_ask?: number | null;
  draw_price?: number | null;
  external_source?: "gs" | "ggs" | null;
  external_match_id?: string | null;
  external_bound?: boolean;
  binding_status?: string | null;
}


export interface UiMatch {
  id: string;
  slug?: string;
  sport: "Football";
  league: string;
  teamA: { id: string; name: string; shortName: string; logoUrl?: string | null };
  teamB: { id: string; name: string; shortName: string; logoUrl?: string | null };
  scoreA: number;
  scoreB: number;
  period: string;
  clock: string;
  status: "Live" | "Scheduled" | "Finished";
  startTime: Date;
  wsTime: Date;
  marketA: { bid: number; ask: number };
  marketB: { bid: number; ask: number };
  marketDraw?: { bid: number; ask: number };
  homePrice?: number | null;
  awayPrice?: number | null;
  drawPrice?: number | null;
  history: { time: number; probabilityA: number }[];
  volume: number;
  moneylineVolume: number;
  totalVolume: number;
  gameId?: number | null;
  externalSource?: "gs" | "ggs" | null;
  externalMatchId?: string | null;
  externalBound: boolean;
  bindingStatus?: string | null;
}

function normalizeBackendMatchStatus(status: string | null | undefined): "Live" | "Scheduled" | "Finished" {
  const normalized = String(status ?? "").trim().toLowerCase();
  if (
    ["live", "inplay", "inprogress", "running", "halftime", "ht"].includes(normalized) ||
    normalized.includes("in progress") ||
    normalized.includes("half time") ||
    normalized.includes("intermission") ||
    normalized.includes("interval") ||
    normalized.includes("break")
  ) {
    return "Live";
  }
  if (["finished", "ended", "closed"].includes(normalized)) {
    return "Finished";
  }
  return "Scheduled";
}

function parseBackendMatchTime(value: string | null | undefined): { period: string; clock: string } {
  const text = String(value ?? "").trim();
  if (!text) {
    return { period: "", clock: "" };
  }
  const tokenized = text.match(/^([A-Za-z0-9]+)\s+(\d{1,2}:\d{2})$/);
  if (tokenized) {
    return { period: tokenized[1], clock: tokenized[2] };
  }
  const clockOnly = text.match(/^(\d{1,2}:\d{2})$/);
  if (clockOnly) {
    return { period: "", clock: clockOnly[1] };
  }
  return { period: text, clock: "" };
}

export function formatLeagueLabel(value: string | null | undefined): string {
  const text = String(value ?? "").trim();
  if (!text) {
    return "";
  }
  const title = text.match(/['"]title['"]\s*:\s*['"]([^'"]+)['"]/);
  if (title?.[1]) {
    return title[1];
  }
  const name = text.match(/['"](?:name|label)['"]\s*:\s*['"]([^'"]+)['"]/);
  if (name?.[1]) {
    return name[1];
  }
  if (text.startsWith("[") || text.startsWith("{")) {
    return "Football";
  }
  return text;
}


export function mapBackendMatchToUiMatch(row: BackendMatchCard): UiMatch {
  const sport = "Football";
  const status = normalizeBackendMatchStatus(row.status);
  const backendMatchTime = parseBackendMatchTime(row.pm_match_time);
  const marketA = {
    bid: Number(row.home_bid ?? row.home_price ?? 0.5),
    ask: Number(row.home_ask ?? row.home_price ?? row.home_bid ?? 0.5),
  };
  const marketB = {
    bid: Number(row.away_bid ?? row.away_price ?? 0),
    ask: Number(row.away_ask ?? row.away_price ?? row.away_bid ?? 0),
  };
  const hasDraw =
    row.draw_bid != null ||
    row.draw_ask != null ||
    row.draw_price != null;
  return {
    id: row.match_id,
    slug: row.external_event_slug ?? undefined,
    sport,
    league: formatLeagueLabel(row.league),
    teamA: { id: `${row.match_id}_home`, name: row.team_home, shortName: row.team_home, logoUrl: row.home_logo_url ?? null },
    teamB: { id: `${row.match_id}_away`, name: row.team_away, shortName: row.team_away, logoUrl: row.away_logo_url ?? null },
    scoreA: Number(row.score_home ?? 0),
    scoreB: Number(row.score_away ?? 0),
    period: backendMatchTime.period,
    clock: backendMatchTime.clock,
    status,
    startTime: new Date(row.start_time_utc),
    wsTime: new Date(row.latest_ts_utc),
    marketA,
    marketB,
    marketDraw: hasDraw
      ? {
          bid: Number(row.draw_bid ?? row.draw_price ?? 0),
          ask: Number(row.draw_ask ?? row.draw_price ?? row.draw_bid ?? 0),
        }
      : undefined,
    homePrice: row.home_price ?? null,
    awayPrice: row.away_price ?? null,
    drawPrice: row.draw_price ?? null,
    history: [],
    volume: row.moneyline_volume,
    moneylineVolume: row.moneyline_volume,
    totalVolume: row.total_volume,
    gameId: row.game_id ?? null,
    externalSource: row.external_source ?? null,
    externalMatchId: row.external_match_id ?? null,
    externalBound: Boolean(row.external_bound),
    bindingStatus: row.binding_status ?? null,
  };
}
