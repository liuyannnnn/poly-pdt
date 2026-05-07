import React, { createContext, useContext, useState, useEffect, ReactNode, useCallback } from "react";
import {
  CollectorSettings,
  MARKET_WS_URL,
  fetchCollectorSettings,
  fetchHistoryMatches,
  fetchMatches,
  saveCollectorSettings,
  startSimulation,
  stopSimulation,
} from "../api/client";
import { mapBackendMatchToUiMatch } from "../api/mappers";
import {
  applyMatchLogToMatches,
  applyTraderEventToMatches,
  applyExternalUpdateToMatches,
  applyWsTicksToMatches,
  mergeMatchesPreservingRealtime,
  reconcileSidebarMatchOrder,
  resolveSelectedMatchId,
  sortMatchesForSidebar,
  type WsTickPayload,
} from "../api/trading-mappers";

export interface Team {
  id: string;
  name: string;
  shortName: string;
  logoUrl?: string | null;
}

export interface Market {
  bid: number;
  ask: number;
}

export interface Match {
  id: string;
  sport: "Football";
  league: string;
  teamA: Team;
  teamB: Team;
  scoreA: number;
  scoreB: number;
  period: string;
  clock: string;
  status: "Live" | "Scheduled" | "Finished";
  startTime: Date;
  wsTime: Date;
  marketA: Market;
  marketB: Market;
  marketDraw?: Market;
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

export interface Position {
  id: string;
  matchId: string;
  teamId: string;
  amount: number;
  entryPrice: number;
  timestamp: number;
  type: "Buy" | "Sell";
}

export interface TradeLog {
  id: string;
  timestamp: number;
  message: string;
  type: "Info" | "Order" | "Alert" | "Error";
}

export interface TradingStrategy {
  name: string;
  retracement: number;
}

interface TradingContextType {
  matches: Match[];
  historyMatches: Match[];
  historyPage: number;
  historyPageSize: number;
  historyHasMore: boolean;
  loadHistoryPage: (page: number) => Promise<void>;
  selectedMatchId: string | null;
  selectMatch: (id: string) => void;
  balance: number;
  positions: Position[];
  tradeLogs: TradeLog[];
  placeOrder: (matchId: string, teamId: string, amount: number, side: "Buy" | "Sell") => void;
  isSimulation: boolean;
  setSimulationMode: (isSim: boolean) => void;
  resetBalance: (amount: number) => void;
  walletConnected: boolean;
  connectWallet: () => void;
  isRunning: boolean;
  startTrading: (initialBalance: number) => void;
  stopTrading: () => void;
  strategy: TradingStrategy;
  updateStrategy: (strategy: TradingStrategy) => void;
  collectorSettings: CollectorSettings;
  updateCollectorSettings: (settings: CollectorSettings) => Promise<void>;
  refreshMatches: () => Promise<void>;
}

const TradingContext = createContext<TradingContextType | undefined>(undefined);

const INITIAL_MATCHES: Match[] = [];
const HISTORY_PAGE_SIZE = 20;

export const TradingProvider = ({ children }: { children: ReactNode }) => {
  const [matches, setMatches] = useState<Match[]>(INITIAL_MATCHES);
  const [historyMatches, setHistoryMatches] = useState<Match[]>([]);
  const [historyPage, setHistoryPage] = useState(0);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [selectedMatchId, setSelectedMatchId] = useState<string | null>(null);
  const [balance, setBalance] = useState(10000);
  const [positions, setPositions] = useState<Position[]>([]);
  const [tradeLogs, setTradeLogs] = useState<TradeLog[]>([]);
  const [isSimulation, setIsSimulation] = useState(true);
  const [walletConnected, setWalletConnected] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [strategy, setStrategy] = useState<TradingStrategy>({ name: "首分买入，回撤卖出", retracement: 0.05 });
  const [collectorSettings, setCollectorSettings] = useState<CollectorSettings>({
    collection_interval_minutes: 5,
    football_volume_threshold_k: 500,
    external_source: "ggs",
  });

  const addLog = (message: string, type: TradeLog["type"]) => {
    setTradeLogs((prev) => [{ id: Math.random().toString(), timestamp: Date.now(), message, type }, ...prev].slice(0, 100));
  };

  const loadHistoryPage = useCallback(async (page: number) => {
    try {
      const safePage = Math.max(0, page);
      const backendHistoryRows = await fetchHistoryMatches(
        HISTORY_PAGE_SIZE,
        safePage * HISTORY_PAGE_SIZE
      );
      const mappedHistory = sortMatchesForSidebar(
        backendHistoryRows.map(mapBackendMatchToUiMatch)
      );
      setHistoryMatches(mappedHistory);
      setHistoryPage(safePage);
      setHistoryHasMore(backendHistoryRows.length === HISTORY_PAGE_SIZE);
    } catch (error) {
      addLog(`拉取历史比赛失败: ${String(error)}`, "Error");
    }
  }, []);

  const loadMatches = useCallback(async () => {
    try {
      const backendRows = await fetchMatches();
      const incomingMatches = backendRows.map(mapBackendMatchToUiMatch);
      let mapped: Match[] = [];
      setMatches((prev) => {
        const merged = mergeMatchesPreservingRealtime(prev, incomingMatches);
        mapped = reconcileSidebarMatchOrder(prev, merged);
        return mapped;
      });
      setSelectedMatchId((current) => resolveSelectedMatchId(current, mapped, historyMatches));
    } catch (error) {
      addLog(`拉取比赛数据失败: ${String(error)}`, "Error");
    }
  }, [historyMatches]);

  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const settings = await fetchCollectorSettings();
        if (!stopped) {
          setCollectorSettings(settings);
        }
      } catch {}
      if (!stopped) {
        await loadMatches();
      }
    };
    void load();
    return () => {
      stopped = true;
    };
  }, [loadMatches]);

  useEffect(() => {
    let stopped = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let pendingMarketTicks = new Map<string, WsTickPayload>();
    let marketTickFlushTimer: number | null = null;

    const flushMarketTicks = () => {
      marketTickFlushTimer = null;
      const ticks = Array.from(pendingMarketTicks.values());
      pendingMarketTicks = new Map();
      if (ticks.length === 0) {
        return;
      }
      setMatches((prev) => {
        const updated = applyWsTicksToMatches(prev, ticks);
        return reconcileSidebarMatchOrder(prev, updated);
      });
    };

    const connect = () => {
      if (stopped) {
        return;
      }
      ws = new WebSocket(MARKET_WS_URL);
      ws.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data);
          if (parsed?.topic === "match.snapshot" && parsed.payload?.match_id) {
            const incoming = mapBackendMatchToUiMatch(parsed.payload);
            setMatches((prev) => {
              const existing = prev.find((row) => row.id === incoming.id);
              const merged = mergeMatchesPreservingRealtime(existing ? [existing] : [], [incoming])[0];
              const nextRows = [
                ...prev.filter((row) => row.id !== incoming.id),
                merged,
              ].filter((row) => row.status !== "Finished");
              return reconcileSidebarMatchOrder(prev, nextRows);
            });
            return;
          }
          if (parsed?.topic === "chart.snapshot" && parsed.payload?.match_id) {
            window.dispatchEvent(new CustomEvent("pdt:chart-snapshot", { detail: parsed.payload }));
            return;
          }
          if (parsed?.topic === "market.tick" && parsed.payload?.match_id) {
            const tick = {
              match_id: String(parsed.payload.match_id),
              outcome: parsed.payload.outcome,
              bid: Number(parsed.payload.bid),
              ask: Number(parsed.payload.ask),
              ts_utc: String(parsed.payload.ts_utc),
              status: parsed.payload.status,
              match_time: parsed.payload.match_time,
              score_home: parsed.payload.score_home,
              score_away: parsed.payload.score_away,
            };
            pendingMarketTicks.set(`${tick.match_id}|${String(tick.outcome ?? "").toLowerCase()}`, tick);
            if (marketTickFlushTimer == null) {
              marketTickFlushTimer = window.setTimeout(flushMarketTicks, 100);
            }
            return;
          }
          if (parsed?.topic === "external.match" && parsed.payload?.guid) {
            window.dispatchEvent(new CustomEvent("pdt:external-match", { detail: parsed.payload }));
            setMatches((prev) => {
              const updated = applyExternalUpdateToMatches(prev, parsed);
              return reconcileSidebarMatchOrder(prev, updated);
            });
            return;
          }
          if (parsed?.topic === "match.log" && parsed.payload?.guid) {
            window.dispatchEvent(new CustomEvent("pdt:match-log", { detail: parsed.payload }));
            setMatches((prev) => {
              const updated = applyMatchLogToMatches(prev, parsed);
              return reconcileSidebarMatchOrder(prev, updated);
            });
            return;
          }
          setMatches((prev) => {
            const updated = applyTraderEventToMatches(prev, parsed);
            return reconcileSidebarMatchOrder(prev, updated);
          });
        } catch {}
      };
      ws.onclose = () => {
        if (!stopped) {
          reconnectTimer = window.setTimeout(connect, 3000);
        }
      };
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer != null) {
        window.clearTimeout(reconnectTimer);
      }
      if (marketTickFlushTimer != null) {
        window.clearTimeout(marketTickFlushTimer);
      }
      ws?.close();
    };
  }, []);

  const placeOrder = (matchId: string, teamId: string, amount: number, side: "Buy" | "Sell") => {
    const match = matches.find((m) => m.id === matchId);
    if (!match) {
      return;
    }
    if (side === "Buy") {
      if (balance < amount) {
        addLog("余额不足", "Error");
        return;
      }
      const price = teamId === "teamB" ? match.marketB.ask : match.marketA.ask;
      setBalance((prev) => prev - amount);
      setPositions((prev) => [
        ...prev,
        { id: Math.random().toString(), matchId, teamId, amount, entryPrice: price, timestamp: Date.now(), type: "Buy" },
      ]);
      addLog(`买入 ${amount} 份额 @ ${(price * 100).toFixed(1)}%`, "Order");
      return;
    }
    const target = positions.find((p) => p.matchId === matchId && p.teamId === teamId);
    if (!target) {
      addLog("无可卖出持仓", "Alert");
      return;
    }
    const price = teamId === "teamB" ? match.marketB.bid : match.marketA.bid;
    const pnl = (price - target.entryPrice) * (target.amount / Math.max(target.entryPrice, 0.000001));
    setBalance((prev) => prev + target.amount + pnl);
    setPositions((prev) => prev.filter((p) => p.id !== target.id));
    addLog(`卖出 ${target.amount} 份额 @ ${(price * 100).toFixed(1)}%, 收益 ${pnl.toFixed(2)}`, "Order");
  };

  const connectWallet = () => {
    setWalletConnected(true);
  };

  const setSimulationMode = (isSim: boolean) => {
    setIsSimulation(isSim);
  };

  const startTrading = (initialBalance: number) => {
    setIsRunning(true);
    setBalance(initialBalance);
    void startSimulation(initialBalance, strategy.retracement)
      .then(() => undefined)
      .catch((error) => addLog(`启动失败: ${String(error)}`, "Error"));
  };

  const stopTrading = () => {
    setIsRunning(false);
    void stopSimulation()
      .then(() => undefined)
      .catch((error) => addLog(`停止失败: ${String(error)}`, "Error"));
  };

  const updateStrategy = (newStrategy: TradingStrategy) => {
    setStrategy(newStrategy);
  };

  const updateCollectorSettings = async (settings: CollectorSettings) => {
    try {
      const saved = await saveCollectorSettings(settings);
      setCollectorSettings(saved);
      await loadMatches();
    } catch (error) {
      addLog(`采集设置保存失败: ${String(error)}`, "Error");
    }
  };

  return (
    <TradingContext.Provider
      value={{
        matches,
        historyMatches,
        historyPage,
        historyPageSize: HISTORY_PAGE_SIZE,
        historyHasMore,
        loadHistoryPage,
        selectedMatchId,
        selectMatch: setSelectedMatchId,
        balance,
        positions,
        tradeLogs,
        placeOrder,
        isSimulation,
        setSimulationMode,
        resetBalance: setBalance,
        walletConnected,
        connectWallet,
        isRunning,
        startTrading,
        stopTrading,
        strategy,
        updateStrategy,
        collectorSettings,
        updateCollectorSettings,
        refreshMatches: loadMatches,
      }}
    >
      {children}
    </TradingContext.Provider>
  );
};

export const useTrading = () => {
  const context = useContext(TradingContext);
  if (!context) {
    throw new Error("useTrading must be used within a TradingProvider");
  }
  return context;
};
