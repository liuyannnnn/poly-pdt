export interface TradingPanelAccount {
  id: string;
  mode: "real" | "simulation";
  strategyKey: string;
  strategyName: string;
  strategyConfig: Record<string, number>;
  strategyParams: { retracement: number };
  initialBalance: number;
  sports: string[];
  totalAssets: number;
  availableCash: number;
  marketValue: number;
  todayProfit: number;
  totalProfit: number;
  winRate: number;
  isRunning: boolean;
  positionCount: number;
  pmAccountId?: string;
  maxPositions: number;
  maxFundUsageRate: number;
  maxSingleOrderPct: number;
  maxAddCount: number;
  maxAddFundPct: number;
  stopLossDrawdown: number;
}

export interface TradingPanelPositionRow {
  id: string;
  orderId: string;
  slug: string;
  matchName: string;
  teamLabel: string;
  teamName: string;
  teamDisplayName: string;
  amount: number;
  shares: number;
  entryPrice: number;
  currentPrice: number;
  profit: number;
  profitPercent: number;
  timestamp: number;
}

export interface TradingPanelTradeRow {
  id: number | string;
  orderId: string;
  strategy: string;
  slug?: string;
  side: string;
  reason?: string;
  entryPrice: number | null;
  exitPrice: number | null;
  quantity: number;
  amount: number;
  profit: number | null;
  profitRate: number | null;
  timestamp: number;
}


const SPORT_LABELS: Record<string, string> = {
  football: "足球",
};

const STRATEGY_LABELS: Record<string, string> = {
  prematch_gap_retracement: "开赛前价差回撤",
  live_first_goal_retracement: "足球首球回撤",
  football_score_delay_trade: "足球-比分时差交易",
  football_winrate_gap_buy: "胜率差买入",
  "test-001": "test-001",
};

function normalizeRealtimeStatus(status: string | null | undefined): "Live" | "Scheduled" | "Finished" {
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

function parseRealtimeMatchTime(value: string | null | undefined): { period?: string; clock?: string } {
  const text = String(value ?? "").trim();
  if (!text) {
    return {};
  }

  const tokenized = text.match(/^([A-Za-z0-9]+)\s+(\d{1,2}:\d{2})$/);
  if (tokenized) {
    return { period: tokenized[1], clock: tokenized[2] };
  }

  const clockOnly = text.match(/^(\d{1,2}:\d{2})$/);
  if (clockOnly) {
    return { clock: clockOnly[1] };
  }

  return { period: text };
}

function timestampMs(value: unknown): number {
  if (!value) return 0;
  const parsed = new Date(String(value)).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function compactOrderId(value: unknown, fallback: string): string {
  const raw = String(value ?? "").trim() || autoOrderId(fallback);
  return raw.slice(0, 16);
}

function autoOrderId(seed: string): string {
  let left = 0x811c9dc5;
  let right = 0x9e3779b9;
  for (let i = 0; i < seed.length; i += 1) {
    const code = seed.charCodeAt(i);
    left ^= code;
    left = Math.imul(left, 0x01000193);
    right ^= code + i;
    right = Math.imul(right, 0x85ebca6b);
  }
  return `${(left >>> 0).toString(16).padStart(8, "0")}${(right >>> 0).toString(16).padStart(8, "0")}`;
}

function outcomeLabel(value: unknown): string {
  const raw = String(value ?? "").trim();
  const lower = raw.toLowerCase();
  if (lower === "home") return "Home";
  if (lower === "away") return "Away";
  if (lower === "draw") return "Draw";
  return raw;
}

function displayTeam(label: string, teamName: string): string {
  if ((label === "Home" || label === "Away") && teamName) {
    return `${label} ${teamName}`;
  }
  return label || teamName || "-";
}

export function mapBackendPositionRow(row: Record<string, unknown>, index = 0): TradingPanelPositionRow {
  const guid = String(row.guid ?? "");
  const outcome = String(row.outcome_key ?? "");
  const fallbackId = `${String(row.trading_id ?? "T")}|${guid}|${outcome}|${index}`;
  const amount = Number(row.amount ?? row.amount_usd ?? row.cost_basis ?? 0);
  const shares = Number(row.shares ?? row.amount ?? 0);
  const profit = Number(row.pnl ?? row.unrealized_pnl ?? 0);
  const label = outcomeLabel(row.team_label ?? row.outcome_key);
  const teamName = String(row.team_name ?? row.outcome_name ?? "");
  return {
    id: String(row.id ?? fallbackId),
    orderId: compactOrderId(row.order_id ?? row.id, fallbackId),
    slug: String(row.slug ?? row.match_slug ?? guid),
    matchName: String(row.slug ?? row.match_slug ?? row.match_name ?? guid),
    teamLabel: label,
    teamName,
    teamDisplayName: displayTeam(label, teamName),
    amount,
    shares,
    entryPrice: Number(row.entry_price ?? row.avg_entry_price ?? row.price ?? 0),
    currentPrice: Number(row.current_price ?? row.current_bid1 ?? row.current_ask1 ?? 0),
    profit,
    profitPercent: amount > 0 ? (profit / amount) * 100 : 0,
    timestamp: timestampMs(row.ts_utc ?? row.opened_at_utc),
  };
}

export function mapBackendTradeRow(row: Record<string, unknown>, index = 0): TradingPanelTradeRow {
  const action = String(row.action ?? row.side ?? "").toLowerCase();
  const actionSuffix = action === "sell" ? "S" : "B";
  const traderId = String(row.trading_id ?? row.trader_id ?? "").toUpperCase();
  const shares = Number(row.shares ?? 0);
  const amount = Number(row.amount_usd ?? row.amount ?? 0);
  const costBasis = Number(row.cost_basis ?? row.invested_amount ?? 0);
  const price = Number(row.price ?? row.cost_price ?? 0);
  const label = outcomeLabel(row.team_label ?? row.outcome_key);
  const teamName = String(row.team_name ?? row.outcome_name ?? "");
  const sideName = displayTeam(label, teamName);
  const profit = action === "sell" ? Number(row.profit ?? 0) : null;
  return {
    id: row.id != null ? String(row.id) : `${String(row.ts_utc ?? index)}|${action}|${traderId || "SYS"}`,
    orderId: compactOrderId(row.order_id ?? row.id, `${row.ts_utc ?? ""}|${row.guid ?? ""}|${traderId}|${action}|${index}`),
    strategy: traderId || "SYS",
    slug: String(row.slug ?? row.match_slug ?? row.guid ?? ""),
    side: `${sideName} / ${actionSuffix}`,
    reason: String(row.reason ?? ""),
    entryPrice: action === "sell" && shares > 0 && costBasis > 0 ? costBasis / shares : price,
    exitPrice: action === "sell" ? price : null,
    quantity: shares,
    amount,
    profit,
    profitRate: action === "sell"
      ? Number(row.profit_rate ?? (costBasis > 0 ? (Number(row.profit ?? 0) / costBasis) * 100 : 0))
      : null,
    timestamp: timestampMs(row.ts_utc),
  };
}

export function formatCompactUsdVolume(value: number): string {
  if (value >= 1_000_000) {
    return `$${(value / 1_000_000).toFixed(2)}M`;
  }
  return `$${(value / 1000).toFixed(2)}K`;
}

export function profitColorClass(profit: number): string {
  if (profit > 0) {
    return "text-red-500";
  }
  if (profit < 0) {
    return "text-green-600";
  }
  return "text-gray-500";
}

export function formatSignedUsd(value: number): string {
  const amount = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (value > 0) {
    return `+$${amount}`;
  }
  if (value < 0) {
    return `-$${amount}`;
  }
  return `$${amount}`;
}

export function displayTradingId(id: string, mode: "real" | "simulation"): string {
  if (/^[SR]\d+$/i.test(id)) {
    return id.toUpperCase();
  }
  return `${mode === "real" ? "R" : "S"}-${id}`;
}

export function resolveTraderInitialBalance(
  mode: "real" | "simulation",
  formInitialBalance: string,
  pmAccount: { total_funds?: number; available_funds?: number } | undefined,
): number {
  if (mode === "real") {
    return Number(pmAccount?.total_funds ?? pmAccount?.available_funds ?? 0);
  }
  const parsed = Number.parseFloat(formInitialBalance);
  return Number.isFinite(parsed) ? parsed : 10000;
}


function getMatchTimeValue(value: unknown): number {
  if (value instanceof Date) {
    return value.getTime();
  }
  if (typeof value === "string" || typeof value === "number") {
    const parsed = new Date(value).getTime();
    return Number.isNaN(parsed) ? 0 : parsed;
  }
  return 0;
}

export function sortMatchesForSidebar<
  T extends { status: string; startTime?: Date | string; wsTime?: Date | string }
>(rows: T[]): T[] {
  const statusRank = (status: string): number => {
    if (status === "Live") return 0;
    if (status === "Scheduled") return 1;
    if (status === "Finished") return 2;
    return 3;
  };

  return [...rows].sort((left, right) => {
    const rankDelta = statusRank(left.status) - statusRank(right.status);
    if (rankDelta !== 0) {
      return rankDelta;
    }
    if (left.status === "Live" || left.status === "Scheduled") {
      return getMatchTimeValue(left.startTime) - getMatchTimeValue(right.startTime);
    }
    return getMatchTimeValue(right.startTime) - getMatchTimeValue(left.startTime);
  });
}

export function mergeMatchesPreservingRealtime<
  T extends {
    id: string;
    wsTime?: Date | string;
    period?: string;
    clock?: string;
    scoreA?: number;
    scoreB?: number;
    marketA?: { bid: number; ask: number };
    marketB?: { bid: number; ask: number };
    marketDraw?: { bid: number; ask: number };
  }
>(previous: T[], incoming: T[]): T[] {
  const previousById = new Map(previous.map((row) => [row.id, row]));
  return incoming.map((next) => {
    const existing = previousById.get(next.id);
    if (!existing) {
      return next;
    }
    if (getMatchTimeValue(existing.wsTime) <= getMatchTimeValue(next.wsTime)) {
      return next;
    }
    return {
      ...next,
      period: existing.period ?? next.period,
      clock: existing.clock ?? next.clock,
      scoreA: existing.scoreA ?? next.scoreA,
      scoreB: existing.scoreB ?? next.scoreB,
      marketA: existing.marketA ?? next.marketA,
      marketB: existing.marketB ?? next.marketB,
      marketDraw: existing.marketDraw ?? next.marketDraw,
      wsTime: existing.wsTime ?? next.wsTime,
    };
  });
}

export function groupMatchesByStatus<T extends { status: string; startTime?: Date | string; wsTime?: Date | string }>(rows: T[]): { live: T[]; pre: T[]; finished: T[] } {
  return {
    live: rows.filter((item) => item.status === "Live"),
    pre: rows.filter((item) => item.status === "Scheduled"),
    finished: rows.filter((item) => item.status === "Finished"),
  };
}

export function reconcileSidebarMatchOrder<
  T extends {
    id: string;
    status: string;
    startTime?: Date | string;
    wsTime?: Date | string;
  }
>(previous: T[], incoming: T[]): T[] {
  if (previous.length === 0) {
    return sortMatchesForSidebar(incoming);
  }

  const previousById = new Map(previous.map((row, index) => [row.id, { row, index }]));
  const promotedLive = incoming.filter((row) => {
    const existing = previousById.get(row.id)?.row;
    return existing && existing.status !== "Live" && row.status === "Live";
  });

  const existingStable = incoming
    .filter((row) => previousById.has(row.id) && !promotedLive.some((item) => item.id === row.id))
    .sort((left, right) => (previousById.get(left.id)?.index ?? 0) - (previousById.get(right.id)?.index ?? 0));

  const newRows = incoming.filter((row) => !previousById.has(row.id));
  const newLive = newRows.filter((row) => row.status === "Live");
  const newScheduled = newRows.filter((row) => row.status === "Scheduled");
  const newFinished = newRows.filter((row) => row.status === "Finished");

  const stableLive = existingStable.filter((row) => row.status === "Live");
  const stableScheduled = existingStable.filter((row) => row.status === "Scheduled");
  const stableFinished = existingStable.filter((row) => row.status === "Finished");

  return [
    ...promotedLive,
    ...stableLive,
    ...newLive,
    ...stableScheduled,
    ...newScheduled,
    ...stableFinished,
    ...newFinished,
  ];
}

export function resolveSelectedMatchId<
  T extends { id: string }
>(currentSelectedId: string | null, rows: T[], preserveRows: T[] = []): string | null {
  if (rows.length === 0) {
    if (currentSelectedId && preserveRows.some((row) => row.id === currentSelectedId)) {
      return currentSelectedId;
    }
    return null;
  }
  if (
    currentSelectedId &&
    (rows.some((row) => row.id === currentSelectedId) ||
      preserveRows.some((row) => row.id === currentSelectedId))
  ) {
    return currentSelectedId;
  }
  return rows[0].id;
}


export function passesCollectorThreshold<
  T extends { sport: string; totalVolume?: number; volume?: number }
>(
  match: T,
  settings: { footballVol: number }
): boolean {
  const totalVolume = Number(match.totalVolume ?? match.volume ?? 0);
  if (match.sport === "Football") {
    return totalVolume >= settings.footballVol * 1000;
  }
  return false;
}


export function mapBackendAccountToTradingAccount(row: {
  id: string;
  mode: "real" | "simulation";
  strategy_name: string;
  strategy_params?: Record<string, unknown>;
  retracement: number;
  initial_balance?: number;
  affect_sports?: string[];
  total_assets: number;
  available_cash: number;
  today_profit?: number;
  position_count: number;
  win_rate?: number;
  is_running: boolean;
  account_alias?: string | null;
}): TradingPanelAccount {
  const rawStrategyParams = row.strategy_params ?? {};
  const numericStrategyParams = Object.fromEntries(
    Object.entries(rawStrategyParams)
      .filter(([, value]) => typeof value === "number")
      .map(([key, value]) => [key, Number(value)])
  );
  const riskParams =
    rawStrategyParams.risk && typeof rawStrategyParams.risk === "object"
      ? rawStrategyParams.risk as Record<string, unknown>
      : {};
  return {
    id: row.id,
    mode: row.mode,
    strategyKey: row.strategy_name,
    strategyName: STRATEGY_LABELS[row.strategy_name] ?? row.strategy_name,
    strategyConfig: numericStrategyParams,
    strategyParams: { retracement: Number((row.retracement * 100).toFixed(2)) },
    initialBalance: Number(row.initial_balance ?? row.total_assets),
    sports: (row.affect_sports ?? []).map((item) => SPORT_LABELS[item] ?? item),
    totalAssets: row.total_assets,
    availableCash: row.available_cash,
    marketValue: Math.max(0, row.total_assets - row.available_cash),
    todayProfit: Number(row.today_profit ?? 0),
    totalProfit: Number((row.total_assets - Number(row.initial_balance ?? row.total_assets)).toFixed(2)),
    winRate: Number(((row.win_rate ?? 0) * 100).toFixed(2)),
    isRunning: row.is_running,
    positionCount: row.position_count,
    pmAccountId: row.account_alias ?? undefined,
    maxPositions: Number(
      riskParams.max_positions ?? rawStrategyParams.maxPositions ?? rawStrategyParams.max_positions ?? 3,
    ),
    maxFundUsageRate: Number(
      riskParams.max_fund_usage_pct ??
        rawStrategyParams.maxFundUsageRate ??
        rawStrategyParams.max_fund_usage_pct ??
        80,
    ),
    maxSingleOrderPct: Number(
      riskParams.max_single_order_pct ??
        riskParams.max_single_order_usd_pct ??
        rawStrategyParams.maxSingleOrderPct ??
        rawStrategyParams.max_single_order_pct ??
        rawStrategyParams.max_single_order_usd_pct ??
        20,
    ),
    maxAddCount: Number(
      riskParams.max_add_count ?? rawStrategyParams.maxAddCount ?? rawStrategyParams.max_add_count ?? 2,
    ),
    maxAddFundPct: Number(
      riskParams.max_add_fund_pct ?? rawStrategyParams.maxAddFundPct ?? rawStrategyParams.max_add_fund_pct ?? 10,
    ),
    stopLossDrawdown: Number(
      riskParams.stop_loss_drawdown ??
        rawStrategyParams.stopLossDrawdown ??
        rawStrategyParams.stop_loss_drawdown ??
        rawStrategyParams.max_drawdown ??
        rawStrategyParams.max_drawdown_pct ??
        0.05,
    ),
  };
}

export function applyTraderEventToMatches<
  T extends {
    id: string;
    scoreA: number;
    scoreB: number;
    status: "Live" | "Scheduled" | "Finished";
    marketA: { bid: number; ask: number };
    marketB: { bid: number; ask: number };
    marketDraw?: { bid: number; ask: number };
    wsTime: Date;
  }
>(
  rows: T[],
  message: {
    topic?: string;
    payload?: {
      event_id?: string;
      local_ts_utc?: string;
      source_ts_utc?: string;
      pm?: {
        status?: string;
        match_time?: string | null;
        home_score?: number | null;
        away_score?: number | null;
        markets?: Array<{
          market_type?: string;
          outcome_key?: string;
          bid1?: number | null;
          ask1?: number | null;
        }>;
      };
    };
  }
): T[] {
  if (message?.topic === "market.tick" && message.payload && "match_id" in (message.payload as any)) {
    const tick = message.payload as any;
	    return applyWsTickToMatches(rows, {
	      match_id: tick.match_id,
	      outcome: tick.outcome,
	      bid: Number(tick.bid),
	      ask: Number(tick.ask),
	      ts_utc: tick.ts_utc,
	      status: tick.status,
	      match_time: tick.match_time,
	      score_home: tick.score_home,
	      score_away: tick.score_away,
	    });
	  }

  if (message?.topic !== "trader.event") {
    return rows;
  }

  const payload = message.payload;
  const eventId = payload?.event_id;
  const pm = payload?.pm;
  if (!eventId || !pm) {
    return rows;
  }

  return rows.map((row) => {
    if (row.id !== eventId) {
      return row;
    }

    const realtimeMatchTime = parseRealtimeMatchTime(pm.match_time);

    const next = {
      ...row,
      scoreA: Number(pm.home_score ?? row.scoreA),
      scoreB: Number(pm.away_score ?? row.scoreB),
      status: normalizeRealtimeStatus(pm.status),
      ...(realtimeMatchTime.period ? { period: realtimeMatchTime.period } : {}),
      ...(realtimeMatchTime.clock ? { clock: realtimeMatchTime.clock } : {}),
      wsTime: new Date(payload.source_ts_utc ?? payload.local_ts_utc ?? row.wsTime.toISOString()),
    };

    for (const market of pm.markets ?? []) {
      if (market.market_type !== "moneyline") {
        continue;
      }
      const bid = Number(market.bid1 ?? 0);
      const ask = Number(market.ask1 ?? market.bid1 ?? 0);
      if (market.outcome_key === "home") {
        next.marketA = { bid, ask };
      } else if (market.outcome_key === "away") {
        next.marketB = { bid, ask };
      } else if (market.outcome_key === "draw" && next.marketDraw) {
        next.marketDraw = { bid, ask };
      }
    }

    return next;
  });
}

export function applyMatchLogToMatches<T extends { id: string; wsTime: Date }>(
  rows: T[],
  message: {
    topic?: string;
    payload?: {
      guid?: string;
      ts_utc?: string;
    };
  }
): T[] {
  if (message?.topic !== "match.log") {
    return rows;
  }
  const guid = message.payload?.guid;
  const ts = message.payload?.ts_utc;
  if (!guid || !ts) {
    return rows;
  }
  const parsed = new Date(ts);
  if (Number.isNaN(parsed.getTime())) {
    return rows;
  }
  return rows.map((row) => {
    if (row.id !== guid || row.wsTime.getTime() >= parsed.getTime()) {
      return row;
    }
    return { ...row, wsTime: parsed };
  });
}

export function applyExternalUpdateToMatches<T extends { id: string; wsTime: Date }>(
  rows: T[],
  message: {
    topic?: string;
    payload?: {
      guid?: string;
      updated_at_utc?: string;
      ts_utc?: string;
    };
  }
): T[] {
  if (message?.topic !== "external.match") {
    return rows;
  }
  const guid = message.payload?.guid;
  const ts = message.payload?.updated_at_utc ?? message.payload?.ts_utc;
  if (!guid || !ts) {
    return rows;
  }
  const parsed = new Date(ts);
  if (Number.isNaN(parsed.getTime())) {
    return rows;
  }
  return rows.map((row) => {
    if (row.id !== guid || row.wsTime.getTime() >= parsed.getTime()) {
      return row;
    }
    return { ...row, wsTime: parsed };
  });
}


export type WsTickPayload = {
  match_id: string;
  outcome?: string;
  bid: number;
  ask: number;
  ts_utc: string;
  status?: string | null;
  match_time?: string | null;
  score_home?: number | null;
  score_away?: number | null;
};

function tickTimestampMs(tick: Pick<WsTickPayload, "ts_utc">): number {
  const value = new Date(tick.ts_utc).getTime();
  return Number.isFinite(value) ? value : 0;
}

export function applyWsTicksToMatches<T extends {
  id: string;
  scoreA?: number;
  scoreB?: number;
  status?: "Live" | "Scheduled" | "Finished";
  period?: string;
  clock?: string;
  marketA: { bid: number; ask: number };
  marketB: { bid: number; ask: number };
  marketDraw?: { bid: number; ask: number };
  wsTime: Date;
}>(rows: T[], ticks: WsTickPayload[]): T[] {
  const latestByOutcome = new Map<string, WsTickPayload>();
  for (const tick of ticks) {
    const key = `${tick.match_id}|${String(tick.outcome ?? "").toLowerCase()}`;
    const previous = latestByOutcome.get(key);
    if (!previous || tickTimestampMs(tick) >= tickTimestampMs(previous)) {
      latestByOutcome.set(key, tick);
    }
  }

  return Array.from(latestByOutcome.values())
    .sort((left, right) => tickTimestampMs(left) - tickTimestampMs(right))
    .reduce((currentRows, tick) => applyWsTickToMatches(currentRows, tick), rows);
}

export function applyWsTickToMatches<T extends {
  id: string;
  scoreA?: number;
  scoreB?: number;
  status?: "Live" | "Scheduled" | "Finished";
  period?: string;
  clock?: string;
  marketA: { bid: number; ask: number };
  marketB: { bid: number; ask: number };
  marketDraw?: { bid: number; ask: number };
  wsTime: Date;
}>(rows: T[], tick: WsTickPayload): T[] {
  const outcome = String(tick.outcome ?? "").trim().toLowerCase();
  return rows.map((row) => {
    if (row.id !== tick.match_id) {
      return row;
    }
    const realtimeMatchTime = parseRealtimeMatchTime(tick.match_time);
    const hasMetadata =
      Boolean(tick.status) ||
      Boolean(tick.match_time) ||
      tick.score_home != null ||
      tick.score_away != null;
    const next = {
      ...row,
      ...(tick.status ? { status: normalizeRealtimeStatus(tick.status) } : {}),
      ...(tick.score_home != null ? { scoreA: Number(tick.score_home) } : {}),
      ...(tick.score_away != null ? { scoreB: Number(tick.score_away) } : {}),
      ...(realtimeMatchTime.period ? { period: realtimeMatchTime.period } : {}),
      ...(realtimeMatchTime.clock ? { clock: realtimeMatchTime.clock } : {}),
      wsTime: new Date(tick.ts_utc),
    };
    if (outcome === "home") {
      return {
        ...next,
        marketA: { bid: tick.bid, ask: tick.ask },
      };
    }
    if (outcome === "away") {
      return {
        ...next,
        marketB: { bid: tick.bid, ask: tick.ask },
      };
    }
    if (outcome === "draw" && row.marketDraw) {
      return {
        ...next,
        marketDraw: { bid: tick.bid, ask: tick.ask },
      };
    }
    return hasMetadata ? next : row;
  });
}
