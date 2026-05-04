import React, { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { motion } from "motion/react";
import { clsx } from "clsx";
import { Maximize2, X } from "lucide-react";
import * as Dialog from "@radix-ui/react-dialog";
import { useTrading } from "../context/TradingContext";
import { fetchExternalSourceMatchDetail, fetchLogs, fetchMatchSnapshots, fetchTrades } from "../api/client";
import { formatCompactUsdVolume } from "../api/trading-mappers";
import { buildMatchPriceChartOption, chartPhaseAvailability, filterSnapshotsForChart, type ChartPhase } from "./match-chart";

// ─── Trade Record Types ────────────────────────────────────────────────
interface TradeRecord {
  id: number | string;
  strategy: string;
  status: "持仓" | "完成";
  side: string;        // team name
  entryPrice: number | null;  // 成本
  currentPrice: number | null; // 当前
  quantity: number;
  amount: number;
  profit: number | null;
  profitRate: number | null;
  timestamp: number;
}

const parseTimestampMs = (value: unknown): number => {
  if (!value) return 0;
  const parsed = new Date(String(value)).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
};

const formatExternalPair = (label: string, value: any): string => {
  if (!value || typeof value !== "object") {
    return "";
  }
  if (value.home == null || value.away == null) {
    return "";
  }
  return `${label} ${value.home}-${value.away}`;
};

const formatLogActor = (log: any): string => {
  const traderId = String(log.trader_id || log.trading_id || "").trim().toUpperCase();
  if (/^[SR]\d+$/i.test(traderId)) {
    return traderId;
  }
  const source = String(log.source || "").trim().toLowerCase();
  const dataSource = String(log.data_source || "").trim().toLowerCase();
  const raw = dataSource || source;
  if (source === "trader") {
    return traderId || "SYS";
  }
  if (source === "discriminator") {
    if (raw.includes("goalserve") || raw === "gs") return "GS";
    if (raw.includes("allsports") || raw.includes("asa")) return "ASA";
    if (raw.includes("pm") || raw.includes("poly")) return "PM";
    return raw ? raw.toUpperCase() : "SYS";
  }
  if (source.includes("system") || source === "sys" || String(log.level || "").toLowerCase() === "error") {
    return "SYS";
  }
  if (raw.includes("goalserve") || raw === "gs") return "GS";
  if (raw.includes("allsports") || raw.includes("asa")) return "ASA";
  if (raw.includes("pm") || raw.includes("poly")) return "PM";
  return raw ? raw.toUpperCase() : "SYS";
};

const logActorColorClass = (actor: string): string => {
  if (actor.startsWith("S")) return "text-blue-400";
  if (actor.startsWith("R")) return "text-green-400";
  if (actor === "SYS") return "text-red-400";
  return "text-emerald-400";
};

const renderLogContent = (content: string) => {
  const scoreChange = content.match(/^(比分变化\s+.+?\s+->\s+)(.+)$/);
  if (scoreChange) {
    return (
      <>
        <span>{scoreChange[1]}</span>
        <span className="text-yellow-300 font-semibold">{scoreChange[2]}</span>
      </>
    );
  }
  const firstScore = content.match(/^(比分\s+)(.+)$/);
  if (firstScore) {
    return (
      <>
        <span>{firstScore[1]}</span>
        <span className="text-yellow-300 font-semibold">{firstScore[2]}</span>
      </>
    );
  }
  if (/^(红牌|点球)/.test(content)) {
    return <span className="text-red-300 font-semibold">{content}</span>;
  }
  return <span>{content}</span>;
};

const normalizeExternalSourceKey = (source: unknown): "asa" | "gs" | "" => {
  const raw = String(source || "").trim().toLowerCase();
  if (raw.includes("allsports") || raw.includes("asa")) return "asa";
  if (raw.includes("goalserve") || raw === "gs") return "gs";
  return "";
};

const fallbackWidgetUrlForSource = (source: "asa" | "gs" | ""): string => {
  if (source === "asa") {
    return "https://allsportsapi.com/widgets/football-soccer/livescore/download";
  }
  if (source === "gs") {
    return "https://www.goalserve.com/en/sport-data-widgets/match-details-widget/details";
  }
  return "";
};

const safeWidgetUrl = (value: unknown, source: "asa" | "gs" | ""): string => {
  const raw = String(value || "").trim();
  if (/^https?:\/\//i.test(raw)) {
    return raw;
  }
  return fallbackWidgetUrlForSource(source);
};

const TeamLogo = ({ url, name }: { url?: string | null; name: string }) => {
  const [failed, setFailed] = useState(false);
  const initial = name.trim().slice(0, 1).toUpperCase() || "?";

  return (
    <div className="h-16 w-16 shrink-0 flex items-center justify-center">
      {url && !failed ? (
        <img
          src={url}
          alt={`${name} logo`}
          className="max-h-full max-w-full object-contain"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="text-xl font-black text-gray-400">{initial}</span>
      )}
    </div>
  );
};

// ─── Component ────────────────────────────────────────────────────────
export const MatchDetail = () => {
  const { matches, historyMatches, selectedMatchId, tradeLogs: contextLogs } = useTrading();
  const [showLogsDialog, setShowLogsDialog] = useState(false);
  const [backendSnapshots, setBackendSnapshots] = useState<any[]>([]);
  const [backendTrades, setBackendTrades] = useState<any[]>([]);
  const [backendLogs, setBackendLogs] = useState<any[]>([]);
  const [externalDetail, setExternalDetail] = useState<any>(null);
  const [chartPhase, setChartPhase] = useState<ChartPhase>("ALL");
  const [showWidgetDialog, setShowWidgetDialog] = useState(false);

  // Search live/scheduled + history matches
  const match = useMemo(
    () =>
      [...matches, ...historyMatches].find((m) => m.id === selectedMatchId),
    [matches, historyMatches, selectedMatchId]
  );

  useEffect(() => {
    if (!match) {
      return;
    }
    setChartPhase(match.status === "Live" ? "LIVE" : "ALL");
  }, [match?.id, match?.status]);

  useEffect(() => {
    if (!selectedMatchId) {
      return;
    }
    let disposed = false;
    const load = async () => {
      try {
        const [snapshots, trades, logs, externalSource] = await Promise.all([
          fetchMatchSnapshots(selectedMatchId, 1000),
          fetchTrades({ matchId: selectedMatchId, limit: 200 }),
          fetchLogs({ matchId: selectedMatchId, limit: 200 }),
          fetchExternalSourceMatchDetail(selectedMatchId),
        ]);
        if (disposed) {
          return;
        }
        setBackendSnapshots(snapshots);
        setBackendTrades(trades);
        setBackendLogs(logs);
        setExternalDetail(externalSource);
      } catch {
        if (!disposed) {
          setBackendSnapshots([]);
          setBackendTrades([]);
          setBackendLogs([]);
          setExternalDetail(null);
        }
      }
    };
    void load();
    return () => {
      disposed = true;
    };
  }, [selectedMatchId]);

  useEffect(() => {
    if (!selectedMatchId || !match) {
      return;
    }
    const handleExternalMatch = (event: Event) => {
      const detail = (event as CustomEvent<any>).detail;
      if (detail?.guid === selectedMatchId) {
        setExternalDetail((previous: any) => ({
          ...(previous || {}),
          ...detail,
          widget_url: detail.widget_url || previous?.widget_url,
          widget_provider_url: detail.widget_provider_url || previous?.widget_provider_url,
        }));
      }
    };
    window.addEventListener("pdt:external-match", handleExternalMatch);
    return () => {
      window.removeEventListener("pdt:external-match", handleExternalMatch);
    };
  }, [selectedMatchId, match?.id]);

  useEffect(() => {
    if (!selectedMatchId) {
      return;
    }
    const handleMatchLog = (event: Event) => {
      const row = (event as CustomEvent<any>).detail;
      if (!row || row.guid !== selectedMatchId) {
        return;
      }
      setBackendLogs((prev) => [...prev, row].slice(-200));
    };
    window.addEventListener("pdt:match-log", handleMatchLog);
    return () => {
      window.removeEventListener("pdt:match-log", handleMatchLog);
    };
  }, [selectedMatchId]);

  useEffect(() => {
    if (!selectedMatchId) {
      return;
    }
    const handleChartSnapshot = (event: Event) => {
      const snapshot = (event as CustomEvent<any>).detail;
      if (!snapshot || snapshot.match_id !== selectedMatchId || !snapshot.snapshot_ts_utc) {
        return;
      }
      setBackendSnapshots((prev) =>
        [...prev, snapshot].sort((a, b) => String(a.snapshot_ts_utc).localeCompare(String(b.snapshot_ts_utc)))
      );
    };
    window.addEventListener("pdt:chart-snapshot", handleChartSnapshot);
    return () => {
      window.removeEventListener("pdt:chart-snapshot", handleChartSnapshot);
    };
  }, [selectedMatchId]);

  // ECharts option — depends on match
  const filteredSnapshots = useMemo(() => {
    if (!match) {
      return [];
    }
    return filterSnapshotsForChart(backendSnapshots, chartPhase);
  }, [backendSnapshots, chartPhase, match]);

  const phaseAvailability = useMemo(() => {
    return chartPhaseAvailability(backendSnapshots, match?.status);
  }, [backendSnapshots, match?.status]);

  const hasLiveChart = useMemo(() => {
    return phaseAvailability.hasLive;
  }, [phaseAvailability.hasLive]);

  const hasAllChart = useMemo(() => {
    return phaseAvailability.hasAll;
  }, [phaseAvailability.hasAll]);

  useEffect(() => {
    if (chartPhase === "LIVE" && !hasLiveChart && hasAllChart) {
      setChartPhase("ALL");
      return;
    }
    if (chartPhase === "ALL" && !hasAllChart && hasLiveChart) {
      setChartPhase("LIVE");
    }
  }, [chartPhase, hasAllChart, hasLiveChart]);

  const chartOption = useMemo(() => {
    if (!match || filteredSnapshots.length === 0) return {};
    return buildMatchPriceChartOption({
      match,
      snapshots: filteredSnapshots,
    });
  }, [match, filteredSnapshots]);

  // Mock trade records
  const tradeRecords = useMemo((): TradeRecord[] => {
    if (!match) return [];
    if (backendTrades.length === 0) return [];
    return backendTrades.map((item, index) => {
      const action = String(item.action ?? item.side ?? "");
      const actionSuffix = action === "sell" ? "S" : "B";
      const rawOutcomeName = String(item.outcome_name || item.outcome_key || "");
      const outcomeName =
        rawOutcomeName === "home"
          ? match.teamA.shortName
          : rawOutcomeName === "away"
            ? match.teamB.shortName
            : rawOutcomeName === "draw"
              ? "Draw"
              : rawOutcomeName;
      const amount = Number(item.amount_usd ?? item.amount ?? 0);
      const costBasis = Number(item.cost_basis ?? item.invested_amount ?? 0);
      const profit = action === "sell" ? Number(item.profit ?? 0) : null;
      return {
        id: item.id ?? `${item.ts_utc || index}|${action}|${item.trading_id || item.trader_id || "SYS"}`,
        strategy: String(item.trading_id || item.trader_id || "SYS").toUpperCase(),
        status: action === "buy" ? "持仓" : "完成",
        side: `${outcomeName} / ${actionSuffix}`,
        entryPrice: Number(item.cost_price ?? item.price ?? 0),
        currentPrice: action === "sell" ? Number(item.price || 0) : null,
        quantity: Number(item.shares || 0),
        amount,
        profit,
        profitRate: action === "sell"
          ? Number(item.profit_rate ?? (costBasis > 0 ? (Number(item.profit ?? 0) / costBasis) * 100 : 0))
          : null,
        timestamp: parseTimestampMs(item.ts_utc),
      };
    });
  }, [match, backendTrades]);

  const tradeLogs = useMemo(() => {
    if (!match) return [];
    if (backendLogs.length > 0) {
      return backendLogs.slice(0, 200).map((log) => ({
        id: log.id ?? `${log.ts_utc || ""}|${log.source || ""}|${log.data_source || ""}|${log.event_kind || log.event_type || ""}|${log.message || ""}`,
        timestamp: parseTimestampMs(log.ts_utc),
        tradeId: formatLogActor(log),
        action: String(log.event_kind || log.level || "").toUpperCase(),
        content: log.message,
      }));
    }
    return contextLogs.slice(0, 100).map((log) => ({
      id: log.id,
      timestamp: log.timestamp,
      tradeId: "SYS",
      action: log.type,
      content: log.message,
    }));
  }, [match, contextLogs, backendLogs]);

  // Early return AFTER all hooks
  if (!match) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        选择比赛查看详情
      </div>
    );
  }

  const isLive = match.status === "Live";
  const isFinished = match.status === "Finished";
  const totalVolume = match.totalVolume;
  const liveMatchTime = [match.period, match.clock].filter(Boolean).join(" ").trim();

  const formatTimestamp = (ts: number) => {
    if (!Number.isFinite(ts) || ts <= 0) {
      return "--";
    }
    const d = new Date(ts);
    return `${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}.${String(d.getMilliseconds()).padStart(3,'0')}`;
  };
  const formatTradeTime = (ts: number) => {
    if (!Number.isFinite(ts) || ts <= 0) {
      return "--";
    }
    const d = new Date(ts);
    return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
  };

  const lastSnapshot = filteredSnapshots[filteredSnapshots.length - 1];
  const teamALast = lastSnapshot
    ? (Number(lastSnapshot.home_ask ?? match.marketA.ask) * 100).toFixed(1)
    : "—";
  const teamBLast = lastSnapshot
    ? (Number(lastSnapshot.away_ask ?? match.marketB.ask) * 100).toFixed(1)
    : "—";
  const drawLast = match.marketDraw
    ? lastSnapshot
      ? (Number(lastSnapshot.draw_ask ?? match.marketDraw.ask) * 100).toFixed(1)
      : "—"
    : null;
  const goalserveScore =
    externalDetail?.score_home != null && externalDetail?.score_away != null
      ? `${externalDetail.score_home} - ${externalDetail.score_away}`
      : "—";
  const externalTeams =
    externalDetail?.home_team && externalDetail?.away_team
      ? `${externalDetail.home_team} / ${externalDetail.away_team}`
      : "";
  const externalUpdatedAt = externalDetail?.updated_at_utc || externalDetail?.updated_at || "";
  const externalStats = [
    externalDetail?.clock ? `时间 ${externalDetail.clock}` : "",
    formatExternalPair("黄牌", externalDetail?.yellow_cards),
    formatExternalPair("红牌", externalDetail?.red_cards),
    formatExternalPair("角球", externalDetail?.corners),
    formatExternalPair("射正", externalDetail?.shots_on_target),
  ].filter(Boolean).join(" / ");
  const externalSourceKey = normalizeExternalSourceKey(externalDetail?.source || match.externalSource);
  const externalSourceLabel =
    externalSourceKey === "asa"
      ? "AllSportsAPI"
      : externalSourceKey === "gs"
        ? "Goalserve"
        : "未绑定";
  const externalWidgetUrl = safeWidgetUrl(
    externalDetail?.widget_url || externalDetail?.widget_provider_url,
    externalSourceKey
  );
  const widgetTitle = `${externalSourceLabel} Widget`;

  // ─── Log rows helper ─────────────────────────────────────────────────
  const LogRow = ({ log }: { log: typeof tradeLogs[0] }) => (
    <div className="text-gray-300 leading-relaxed select-text">
      <span className="text-gray-500">[{formatTimestamp(log.timestamp)}]</span>{" "}
      <span className={clsx("font-semibold", logActorColorClass(log.tradeId))}>
        [{log.tradeId}]
      </span>{" "}
      <span className="text-gray-300">{log.action}:</span>{" "}
      <span className="text-gray-200">{renderLogContent(String(log.content || ""))}</span>
    </div>
  );

  return (
    <div className="bg-gray-50 flex flex-col h-full overflow-y-auto custom-scrollbar">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="p-4 bg-white border-b border-gray-200 m-[0px]">
        {/* League row */}
        <div className="flex items-center mb-2">
          <div className="flex items-center gap-2">
            <span className="bg-gray-50 px-2 py-0.5 rounded-md text-xs font-medium text-gray-600 border border-gray-200">
              ⚽{" "}{match.sport}
            </span>
            <span className="text-sm text-gray-500">{match.league}</span>
          </div>
        </div>

        {/* Status & clock */}
        <div className="flex justify-center items-center gap-3 mb-2">
          {isLive ? (
            <div className="flex items-center gap-1.5 bg-red-50 px-2 py-1 rounded-md border border-red-100">
              <motion.div animate={{ opacity: [1, 0.2, 1] }} transition={{ repeat: Infinity, duration: 1.5 }} className="w-2 h-2 rounded-full bg-red-500" />
              <span className="text-xs font-bold text-red-500">LIVE</span>
            </div>
          ) : isFinished ? (
            <div className="bg-gray-100 px-2 py-1 rounded-md border border-gray-200">
              <span className="text-xs font-bold text-gray-400">END</span>
            </div>
          ) : (
            <div className="bg-blue-50 px-2 py-1 rounded-md border border-blue-100">
              <span className="text-xs font-bold text-blue-500">PRE-MATCH</span>
            </div>
          )}
          <span className="text-sm text-[#10b981] font-medium min-w-[120px] text-left">
            {isLive ? (liveMatchTime || "--") : match.startTime.toLocaleString()}
          </span>
        </div>

        {/* Score */}
        <div className="grid grid-cols-[minmax(260px,1fr)_auto_minmax(260px,1fr)] items-center gap-x-8 mb-3 max-w-[1040px] mx-auto">
          <div className="flex items-center justify-end gap-4 min-w-0">
            <TeamLogo url={match.teamA.logoUrl} name={match.teamA.name} />
            <div className="text-right min-w-0">
              <div className="text-xl font-bold text-gray-900 truncate">{match.teamA.name}</div>
            </div>
          </div>
          <div className="flex items-center justify-center gap-4 text-5xl font-black tracking-tighter">
            <span className="text-gray-900">{match.scoreA}</span>
            <span className="text-gray-300 text-3xl">-</span>
            <span className="text-gray-900">{match.scoreB}</span>
          </div>
          <div className="flex items-center justify-start gap-4 min-w-0">
            <div className="text-left min-w-0">
              <div className="text-xl font-bold text-gray-900 truncate">{match.teamB.name}</div>
            </div>
            <TeamLogo url={match.teamB.logoUrl} name={match.teamB.name} />
          </div>
        </div>

        {/* Poly info */}
        <div className="bg-gray-50 py-2 px-4 flex justify-between text-xs text-gray-400">
          <span>Slug: {match.slug ?? "—"}</span>
          <span>Game ID: {match.gameId ?? "—"}</span>
        </div>
      </div>

      {/* ── ECharts Chart ──────────────────────────────────────────────── */}
      <div className="p-4 bg-white border-b border-gray-200 mt-2">
        <div className="flex justify-end mb-3">
          <div className="inline-flex rounded-md border border-gray-200 overflow-hidden">
            <button
              type="button"
              onClick={() => {
                if (hasAllChart) {
                  setChartPhase("ALL");
                }
              }}
              disabled={!hasAllChart}
              className={clsx(
                "px-3 py-1.5 text-xs font-semibold transition-colors",
                chartPhase === "ALL" ? "bg-gray-900 text-white" : "bg-white text-gray-500 hover:bg-gray-50",
                !hasAllChart && "cursor-not-allowed text-gray-300 hover:bg-white"
              )}
            >
              ALL
            </button>
              <button
                type="button"
                onClick={() => {
                if (hasLiveChart) {
                  setChartPhase("LIVE");
                }
              }}
              disabled={!hasLiveChart}
              className={clsx(
                "px-3 py-1.5 text-xs font-semibold border-l border-gray-200 transition-colors",
                chartPhase === "LIVE" ? "bg-gray-900 text-white" : "bg-white text-gray-500 hover:bg-gray-50",
                !hasLiveChart && "cursor-not-allowed text-gray-300 hover:bg-white"
              )}
            >
              LIVE
            </button>
          </div>
        </div>
        {filteredSnapshots.length > 0 ? (
          <div className="rounded-xl overflow-hidden bg-transparent">
            <ReactECharts
              key={`${match.id}-${chartPhase}`}
              option={chartOption}
              style={{ height: "300px", width: "100%" }}
              notMerge={true}
              lazyUpdate={true}
            />
          </div>
        ) : (
          <div className="h-64 flex items-center justify-center text-gray-400 text-sm bg-gray-50 rounded-md">
            暂无{chartPhase}图表数据
          </div>
        )}

        {/* Chart footer: total volume (left) + legend (right) */}
        <div className="flex justify-between items-center mt-2 pt-2 border-t border-gray-100">
          <div className="text-xs text-gray-500">
            Total Vol: <span className="text-gray-700 font-medium">{formatCompactUsdVolume(totalVolume)}</span>
          </div>
          <div className="flex gap-4">
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-0.5 bg-[#0b6ea8] rounded"></div>
              <span className="text-xs text-gray-600">{match.teamA.shortName} {teamALast}%</span>
            </div>
            {match.marketDraw && (
              <div className="flex items-center gap-1.5">
                <div className="w-2.5 h-0.5 bg-[#94a3b8] rounded"></div>
                <span className="text-xs text-gray-600">Draw {drawLast}%</span>
              </div>
            )}
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-0.5 bg-[#8ad7de] rounded"></div>
              <span className="text-xs text-gray-600">{match.teamB.shortName} {teamBLast}%</span>
            </div>
          </div>
        </div>
      </div>

      <div className="p-4 bg-white border-b border-gray-200 mt-2">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-700">外部数据源（{externalSourceLabel}）</h3>
          {!externalDetail && (
            <span className="text-[11px] text-gray-400">未接入 / 暂无数据</span>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3 text-xs">
          <div className="bg-gray-50 border border-gray-200 rounded-md p-2">
            <div className="text-gray-400 mb-1">更新时间</div>
            <div className="text-gray-700 font-medium">
              {externalUpdatedAt ? new Date(externalUpdatedAt).toLocaleString() : "—"}
            </div>
          </div>
          <div className="bg-gray-50 border border-gray-200 rounded-md p-2">
            <div className="text-gray-400 mb-1">比分</div>
            <div className="text-gray-700 font-medium">{goalserveScore}</div>
          </div>
          <div className="bg-gray-50 border border-gray-200 rounded-md p-2">
            <div className="text-gray-400 mb-1">匹配队伍</div>
            <div className="text-gray-700 font-medium">
              {externalTeams || "—"}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setShowWidgetDialog(true)}
            className="bg-gray-50 border border-gray-200 rounded-md p-2 text-left text-xs cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-300"
          >
            <div className="text-gray-400 mb-1 text-xs">赛况</div>
            <div className="text-gray-700 font-medium text-xs">
              {externalStats || "—"}
            </div>
          </button>
        </div>
      </div>

      {/* ── Trade Records ──────────────────────────────────────────────── */}
      <div className="bg-white border-b border-gray-200 mt-2">
        <div className="px-4 py-2.5 border-b border-gray-100 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-700">交易记录</h3>
          <span className="text-[11px] text-gray-400">{tradeRecords.length} 条</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
	            <thead>
	              <tr className="border-b border-gray-100 text-gray-400">
	                <th className="px-3 py-2 text-left font-medium">时间</th>
	                <th className="px-3 py-2 text-left font-medium">交易员</th>
	                <th className="px-3 py-2 text-left font-medium">状态</th>
	                <th className="px-3 py-2 text-left font-medium">方向</th>
	                <th className="px-3 py-2 text-right font-medium">成本</th>
                <th className="px-3 py-2 text-right font-medium">当前</th>
                <th className="px-3 py-2 text-right font-medium">数量</th>
                <th className="px-3 py-2 text-right font-medium">金额</th>
                <th className="px-3 py-2 text-right font-medium">收益</th>
              </tr>
            </thead>
            <tbody>
              {tradeRecords.map((rec) => {
                const isBuy = rec.profit == null || rec.profitRate == null;
                const isProfit = (rec.profit ?? 0) >= 0;
                const profitColor = isBuy ? "text-gray-400" : isProfit ? "text-red-500" : "text-green-600";
	                return (
	                  <tr key={rec.id} className="border-b border-gray-50 hover:bg-gray-50 transition-colors">
	                    <td className="px-3 py-2 text-gray-400 font-mono whitespace-nowrap">{formatTradeTime(rec.timestamp)}</td>
	                    <td className="px-3 py-2">
	                      <span className={clsx(
                        "font-mono font-semibold text-[11px]",
                        rec.strategy.startsWith("R") ? "text-green-600" : "text-blue-500"
                      )}>
                        {rec.strategy}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <span className={clsx(
                        "px-1.5 py-0.5 rounded text-[10px] font-medium",
                        rec.status === "持仓"
                          ? "bg-blue-50 text-blue-600 border border-blue-100"
                          : "bg-gray-100 text-gray-500 border border-gray-200"
                      )}>
                        {rec.status}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <span className="text-[11px] text-gray-700 font-medium whitespace-nowrap">
                        {rec.side}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right text-gray-500 font-mono">{rec.entryPrice == null ? "—" : rec.entryPrice.toFixed(3)}</td>
                    <td className="px-3 py-2 text-right text-gray-700 font-mono">{rec.currentPrice == null ? "—" : rec.currentPrice.toFixed(3)}</td>
                    <td className="px-3 py-2 text-right text-gray-600">{Math.round(rec.quantity).toLocaleString()}</td>
                    <td className="px-3 py-2 text-right text-gray-600">${rec.amount.toFixed(2)}</td>
                    <td className={clsx("px-3 py-2 text-right font-medium whitespace-nowrap", profitColor)}>
                      {isBuy ? "—" : `${isProfit ? "+" : ""}${(rec.profit ?? 0).toFixed(2)}(${isProfit ? "+" : ""}${(rec.profitRate ?? 0).toFixed(2)}%)`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Trade Logs ─────────────────────────────────────────────────── */}
      <div className="h-[400px] mt-2 px-0 pb-4 flex-shrink-0">
        <div className="h-full overflow-hidden flex flex-col bg-gray-900 border-2 border-gray-700 rounded-lg shadow-lg">
          <div className="px-4 py-2 border-b border-gray-700 bg-gray-800 flex justify-between items-center">
            <h3 className="text-sm font-semibold text-gray-200">比赛 / 策略日志</h3>
            <button
              onClick={() => setShowLogsDialog(true)}
              className="text-gray-400 hover:text-gray-200 cursor-pointer transition-colors p-1 rounded hover:bg-gray-700"
            >
              <Maximize2 size={14} />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-3 bg-gray-900 custom-scrollbar select-text">
            <div className="space-y-0.5 font-mono text-[11px]">
              {tradeLogs.map((log) => <LogRow key={log.id} log={log} />)}
            </div>
          </div>
        </div>
      </div>

      {/* ── Full-screen Logs Dialog ─────────────────────────────────────── */}
      <Dialog.Root open={showLogsDialog} onOpenChange={setShowLogsDialog}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-gray-900/80 z-50 backdrop-blur-sm" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-gray-900 border-2 border-gray-700 rounded-lg shadow-2xl w-[90vw] h-[90vh] z-50 flex flex-col">
            <div className="px-6 py-3 border-b border-gray-700 bg-gray-800 flex justify-between items-center">
              <Dialog.Title className="text-base font-semibold text-gray-200">
                交易策略日志 - 全屏查看
              </Dialog.Title>
              <Dialog.Close className="text-gray-400 hover:text-gray-200 cursor-pointer transition-colors">
                <X size={20} />
              </Dialog.Close>
            </div>
            <Dialog.Description className="sr-only">交易策略日志的全屏查看界面</Dialog.Description>
            <div className="flex-1 overflow-y-auto p-6 bg-gray-900 custom-scrollbar select-text">
              <div className="space-y-1 font-mono text-sm">
                {tradeLogs.map((log) => <LogRow key={log.id} log={log} />)}
              </div>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={showWidgetDialog} onOpenChange={setShowWidgetDialog}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-gray-900/70 z-50 backdrop-blur-sm" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white border border-gray-200 rounded-lg shadow-2xl w-[92vw] max-w-5xl h-[86vh] z-50 flex flex-col overflow-hidden">
            <div className="px-5 py-3 border-b border-gray-200 flex justify-between items-center">
              <div>
                <Dialog.Title className="text-base font-semibold text-gray-900">
                  {widgetTitle}
                </Dialog.Title>
                <Dialog.Description className="text-xs text-gray-400 mt-0.5">
                  {match.teamA.name} vs {match.teamB.name}
                </Dialog.Description>
              </div>
              <Dialog.Close className="text-gray-400 hover:text-gray-700 cursor-pointer transition-colors">
                <X size={20} />
              </Dialog.Close>
            </div>
            <div className="flex-1 bg-gray-50">
              {externalWidgetUrl ? (
                <iframe
                  title={widgetTitle}
                  src={externalWidgetUrl}
                  className="h-full w-full border-0 bg-white"
                  referrerPolicy="no-referrer-when-downgrade"
                  allow="fullscreen"
                />
              ) : (
                <div className="h-full flex items-center justify-center text-sm text-gray-400">
                  当前比赛未绑定外部数据源
                </div>
              )}
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
};
