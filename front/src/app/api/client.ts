import { BackendMatchCard } from "./mappers";


const API_PREFIX = "/api/v1";
const DEFAULT_FRONTEND_PORT = "8088";

function readViteEnv(key: string): string | undefined {
  return (import.meta.env as Record<string, string | undefined>)[key];
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function defaultApiBase(): string {
  return API_PREFIX;
}

function resolveApiBase(): string {
  return trimTrailingSlash(readViteEnv("VITE_API_BASE_URL") || defaultApiBase());
}

function resolveMarketWsUrl(apiBase: string): string {
  const configured = readViteEnv("VITE_MARKET_WS_URL");
  if (configured) {
    return configured;
  }
  if (!apiBase.startsWith("http")) {
    if (typeof window === "undefined" || !window.location?.host) {
      return `ws://127.0.0.1:${DEFAULT_FRONTEND_PORT}${API_PREFIX}/ws/market`;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${API_PREFIX}/ws/market`;
  }
  return `${apiBase.replace(/^http/, "ws")}/ws/market`;
}

export const API_BASE = resolveApiBase();
export const MARKET_WS_URL = resolveMarketWsUrl(API_BASE);

export interface AuthSession {
  authenticated: boolean;
  rotation_days: number;
  expires_at_utc?: string | null;
}

export interface CollectorSettings {
  collection_interval_minutes: number;
  football_volume_threshold_k: number;
  external_source: "gs" | "ggs" | "none";
}

export interface CollectorStatus {
  collector_running?: boolean;
  collector_last_run_at?: string | null;
  collector_last_success_at?: string | null;
  collector_last_error?: string | null;
  collector_next_run_at?: string | null;
  external_stream_enabled: boolean;
  external_stream_started: boolean;
  polymarket_ws_enabled: boolean;
  goalserve_ws_enabled: boolean;
  polymarket_ws_connected: boolean;
  pm_market_ws_enabled?: boolean;
  pm_market_ws_connected?: boolean;
  pm_user_ws_enabled?: boolean;
  pm_user_ws_connected?: boolean;
  pm_sports_ws_enabled?: boolean;
  pm_sports_ws_connected?: boolean;
  gs_ws_enabled?: boolean;
  gs_ws_connected?: boolean;
  ggs_ws_enabled?: boolean;
  ggs_ws_connected?: boolean;
  ggs_last_connected_at?: string | null;
  ggs_last_event_at?: string | null;
  ggs_last_error?: string | null;
  polymarket_last_connected_at?: string | null;
  polymarket_last_event_at?: string | null;
  polymarket_last_error?: string | null;
  goalserve_connected: boolean;
  goalserve_transport?: string | null;
  goalserve_last_connected_at?: string | null;
  goalserve_last_event_at?: string | null;
  goalserve_last_error?: string | null;
  matches_count: number;
  last_tick_source: string | null;
  latest_tick_ts_utc: string | null;
}

export interface StrategyCatalogParam {
  key: string;
  display_name: string;
  value_type: string;
  required: boolean;
  default?: number | null;
  min?: number | null;
  max?: number | null;
  decimals?: number | null;
  unit?: string | null;
  description: string;
}

export interface StrategyCatalogItem {
  key: string;
  display_name: string;
  supported_sports: string[];
  params: StrategyCatalogParam[];
}

export interface TradingCreatePayload {
  strategy_name: string;
  strategy_params: Record<string, unknown>;
  affect_sports: string[];
  mode: "simulation" | "real";
  account_alias?: string;
}

export interface TradingUpdatePayload {
  strategy_params?: Record<string, unknown>;
  affect_sports?: string[];
}

export interface BackendTradingAccount {
  id: string;
  mode: "simulation" | "real";
  strategy_name: string;
  strategy_params?: Record<string, unknown>;
  retracement: number;
  initial_balance: number;
  affect_sports: string[];
  total_assets: number;
  available_cash: number;
  today_profit?: number;
  position_count: number;
  win_rate: number;
  is_running: boolean;
  account_alias?: string | null;
}

export interface PMAccountSummary {
  id: string;
  name: string;
  host?: string | null;
  chain_id: number;
  funder_configured: boolean;
  private_key_configured: boolean;
  api_credentials_configured: boolean;
  relayer_configured?: boolean;
  live_trading_enabled: boolean;
  total_funds: number;
  position_funds: number;
  available_funds: number;
  balance_error?: string;
}

export interface TradingSnapshot {
  trading_id: string;
  status: string;
  mode: "simulation" | "real";
  strategy_name: string;
  strategy_params?: Record<string, unknown>;
  affect_sports?: string[];
}

export interface BackendMatchSnapshot {
  match_id: string;
  snapshot_ts_utc: string;
  source: string;
  sport: string;
  status: string;
  phase: string;
  start_time_utc: string;
  team_home: string;
  team_away: string;
  external_event_id?: string;
  external_event_slug?: string;
  external_market_id?: string;
  external_market_slug?: string;
  game_id?: number | null;
  score_home?: number | null;
  score_away?: number | null;
  home_bid?: number | null;
  home_ask?: number | null;
  home_price?: number | null;
  away_bid?: number | null;
  away_ask?: number | null;
  away_price?: number | null;
  draw_bid?: number | null;
  draw_ask?: number | null;
  draw_price?: number | null;
  moneyline_volume?: number | null;
  total_volume?: number | null;
  ingest_type?: string;
}

export interface ExternalMatchCandidate {
  source: "gs" | "ggs";
  external_match_id: string;
  league: string;
  home_team: string;
  away_team: string;
  start_time_utc: string;
  status: string;
  score_home?: number | null;
  score_away?: number | null;
  match_time?: string | null;
  confidence: number;
  team_confidence?: number;
  time_delta_seconds: number;
}


function buildQuery(params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined) {
      return;
    }
    query.set(key, String(value));
  });
  const suffix = query.toString();
  return suffix.length > 0 ? `?${suffix}` : "";
}

function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetch(input, {
    ...init,
    credentials: "include",
  });
}

export async function checkAuthSession(): Promise<AuthSession> {
  const response = await apiFetch(`${API_BASE}/auth/session`);
  if (!response.ok) {
    throw new Error(`auth session request failed: ${response.status}`);
  }
  return response.json();
}

export async function loginWithPassword(password: string): Promise<AuthSession> {
  const response = await apiFetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    throw new Error(`login failed: ${response.status}`);
  }
  return response.json();
}

export async function logout(): Promise<AuthSession> {
  const response = await apiFetch(`${API_BASE}/auth/logout`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`logout failed: ${response.status}`);
  }
  return response.json();
}


export async function fetchMatches(): Promise<BackendMatchCard[]> {
  const response = await apiFetch(`${API_BASE}/matches`);
  if (!response.ok) {
    throw new Error(`matches request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchHistoryMatches(limit = 50, offset = 0): Promise<BackendMatchCard[]> {
  const response = await apiFetch(`${API_BASE}/matches/history?limit=${limit}&offset=${offset}`);
  if (!response.ok) {
    throw new Error(`history matches request failed: ${response.status}`);
  }
  return response.json();
}


export async function fetchTicks(matchId: string): Promise<Array<{ time: string; bid: number; ask: number }>> {
  const response = await apiFetch(`${API_BASE}/ticks?match_id=${encodeURIComponent(matchId)}`);
  if (!response.ok) {
    throw new Error(`ticks request failed: ${response.status}`);
  }
  const rows = await response.json();
  return rows.map((item: any) => ({
    time: item.ts_utc,
    bid: Number(item.bid),
    ask: Number(item.ask),
  }));
}


export async function fetchMatchSnapshots(
  matchId: string,
  series: "all" | "live" = "all",
  limit?: number
): Promise<BackendMatchSnapshot[]> {
  const params = new URLSearchParams({ series });
  if (limit != null) {
    params.set("limit", String(limit));
  }
  const response = await apiFetch(`${API_BASE}/matches/${encodeURIComponent(matchId)}/snapshots?${params.toString()}`);
  if (!response.ok) {
    throw new Error(`match snapshots request failed: ${response.status}`);
  }
  return response.json();
}


export async function fetchAccounts(): Promise<BackendTradingAccount[]> {
  const response = await apiFetch(`${API_BASE}/accounts`);
  if (!response.ok) {
    throw new Error(`accounts request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchPmAccounts(): Promise<PMAccountSummary[]> {
  const response = await apiFetch(`${API_BASE}/pm/accounts`);
  if (!response.ok) {
    throw new Error(`PM accounts request failed: ${response.status}`);
  }
  return response.json();
}


export async function fetchPositions(): Promise<any[]> {
  const response = await apiFetch(`${API_BASE}/positions`);
  if (!response.ok) {
    throw new Error(`positions request failed: ${response.status}`);
  }
  return response.json();
}


export async function fetchTrades(params: { tradingId?: string; matchId?: string; limit?: number } = {}): Promise<any[]> {
  const response = await apiFetch(
    `${API_BASE}/trades${buildQuery({
      trading_id: params.tradingId,
      match_id: params.matchId,
      limit: params.limit,
    })}`
  );
  if (!response.ok) {
    throw new Error(`trades request failed: ${response.status}`);
  }
  return response.json();
}


export async function fetchLogs(params: { tradingId?: string; matchId?: string; limit?: number } = {}): Promise<any[]> {
  const response = await apiFetch(
    `${API_BASE}/logs${buildQuery({
      trading_id: params.tradingId,
      match_id: params.matchId,
      limit: params.limit ?? 200,
    })}`
  );
  if (!response.ok) {
    throw new Error(`logs request failed: ${response.status}`);
  }
  return response.json();
}


export async function fetchExternalSourceMatchDetail(matchId: string): Promise<any> {
  const response = await apiFetch(`${API_BASE}/external-source/match/${encodeURIComponent(matchId)}`);
  if (!response.ok) {
    throw new Error(`external source detail request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchExternalMatchCandidates(
  matchId: string,
  source?: "gs" | "ggs",
  limit = 50
): Promise<ExternalMatchCandidate[]> {
  const response = await apiFetch(
    `${API_BASE}/matches/${encodeURIComponent(matchId)}/external-candidates${buildQuery({
      source,
      limit,
    })}`
  );
  if (!response.ok) {
    throw new Error(`external candidates request failed: ${response.status}`);
  }
  return response.json();
}

export async function bindExternalMatch(
  matchId: string,
  source: "gs" | "ggs",
  externalMatchId: string
): Promise<any> {
  const response = await apiFetch(`${API_BASE}/matches/${encodeURIComponent(matchId)}/external-bind`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, external_match_id: externalMatchId }),
  });
  if (!response.ok) {
    throw new Error(`external bind request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchGoalserveMatchDetail(matchId: string): Promise<any> {
  const response = await apiFetch(`${API_BASE}/goalserve/match/${encodeURIComponent(matchId)}`);
  if (!response.ok) {
    throw new Error(`goalserve detail request failed: ${response.status}`);
  }
  return response.json();
}


export async function fetchCollectorSettings(): Promise<CollectorSettings> {
  const response = await apiFetch(`${API_BASE}/settings/collector`);
  if (!response.ok) {
    throw new Error(`collector settings request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchCollectorStatus(): Promise<CollectorStatus> {
  const response = await apiFetch(`${API_BASE}/collector/status`);
  if (!response.ok) {
    throw new Error(`collector status request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchStrategyCatalog(): Promise<StrategyCatalogItem[]> {
  const response = await apiFetch(`${API_BASE}/strategies/catalog`);
  if (!response.ok) {
    throw new Error(`strategy catalog request failed: ${response.status}`);
  }
  return response.json();
}


export async function saveCollectorSettings(payload: CollectorSettings): Promise<CollectorSettings> {
  const response = await apiFetch(`${API_BASE}/settings/collector`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`save collector settings failed: ${response.status}`);
  }
  return response.json();
}


export async function startSimulation(initialBalance: number, retracement: number): Promise<{ running: boolean }> {
  const response = await apiFetch(`${API_BASE}/simulation/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initial_balance: initialBalance, retracement }),
  });
  if (!response.ok) {
    throw new Error(`start simulation failed: ${response.status}`);
  }
  return response.json();
}


export async function stopSimulation(): Promise<{ running: boolean }> {
  const response = await apiFetch(`${API_BASE}/simulation/stop`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`stop simulation failed: ${response.status}`);
  }
  return response.json();
}

export async function createTrading(payload: TradingCreatePayload): Promise<TradingSnapshot> {
  const response = await apiFetch(`${API_BASE}/tradings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`create trading failed: ${response.status}`);
  }
  return response.json();
}

export async function updateTradingInstance(tradingId: string, payload: TradingUpdatePayload): Promise<TradingSnapshot> {
  const response = await apiFetch(`${API_BASE}/tradings/${encodeURIComponent(tradingId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`update trading failed: ${response.status}`);
  }
  return response.json();
}

export async function startTradingInstance(tradingId: string): Promise<TradingSnapshot> {
  const response = await apiFetch(`${API_BASE}/tradings/${encodeURIComponent(tradingId)}/start`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`start trading failed: ${response.status}`);
  }
  return response.json();
}

export async function stopTradingInstance(tradingId: string): Promise<TradingSnapshot> {
  const response = await apiFetch(`${API_BASE}/tradings/${encodeURIComponent(tradingId)}/stop`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`stop trading failed: ${response.status}`);
  }
  return response.json();
}

export async function deleteTradingInstance(tradingId: string): Promise<{ deleted: boolean }> {
  const response = await apiFetch(`${API_BASE}/tradings/${encodeURIComponent(tradingId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`delete trading failed: ${response.status}`);
  }
  return response.json();
}
