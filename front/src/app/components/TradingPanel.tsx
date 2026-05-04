import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTrading } from '../context/TradingContext';
import { Play, Pause, X, Plus, Trash2, ChevronDown, Wallet, TrendingUp, DollarSign, Search, ChevronLeft, ChevronRight } from 'lucide-react';
import { clsx } from 'clsx';
import * as Dialog from '@radix-ui/react-dialog';
import * as Tooltip from '@radix-ui/react-tooltip';
import { PMAccountSummary, StrategyCatalogItem, createTrading, deleteTradingInstance, fetchAccounts, fetchPmAccounts, fetchPositions, fetchStrategyCatalog, fetchTrades, startTradingInstance, stopTradingInstance, updateTradingInstance } from '../api/client';
import {
  displayTradingId,
  formatSignedUsd,
  mapBackendAccountToTradingAccount,
  mapBackendPositionRow,
  mapBackendTradeRow,
  profitColorClass,
  resolveTraderInitialBalance,
  type TradingPanelPositionRow,
  type TradingPanelTradeRow,
} from '../api/trading-mappers';

// ─── Types ─────────────────────────────────────────────────────────────────
interface TradingAccount {
  id: string;
  mode: 'real' | 'simulation';
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

type StrategyParamFormState = Record<string, string>;

type Position = TradingPanelPositionRow;
type TradeRecord = TradingPanelTradeRow;

const MOCK_TRADE_RECORDS: TradeRecord[] = [
  { id:  1, orderId: 'ORD-20260311-001', strategy: 'R001', side: 'Man Utd',     entryPrice: 0.625, exitPrice: 0.650, quantity: 500, amount: 312.50, profit:  47.30, profitRate:  15.14, timestamp: Date.now() - 86400000*2 },
  { id:  2, orderId: 'ORD-20260311-002', strategy: 'S001', side: 'Arsenal',     entryPrice: 0.580, exitPrice: 0.570, quantity: 200, amount: 145.00, profit:  -8.70, profitRate:  -6.00, timestamp: Date.now() - 86400000*2 + 3600000 },
  { id:  3, orderId: 'ORD-20260311-003', strategy: 'S002', side: 'Real Madrid', entryPrice: 0.650, exitPrice: 0.700, quantity: 800, amount: 520.00, profit:  83.20, profitRate:  16.00, timestamp: Date.now() - 86400000*2 + 7200000 },
  { id:  4, orderId: 'ORD-20260311-004', strategy: 'R001', side: 'Celtics',     entryPrice: 0.420, exitPrice: 0.455, quantity: 600, amount: 252.00, profit:  21.00, profitRate:   8.33, timestamp: Date.now() - 86400000*2 + 10800000 },
  { id:  5, orderId: 'ORD-20260311-005', strategy: 'S001', side: 'Barcelona',   entryPrice: 0.510, exitPrice: 0.490, quantity: 400, amount: 204.00, profit: -20.40, profitRate:  -8.00, timestamp: Date.now() - 86400000*1 },
  { id:  6, orderId: 'ORD-20260312-001', strategy: 'R001', side: 'Lakers',      entryPrice: 0.480, exitPrice: 0.510, quantity: 350, amount: 168.00, profit:  17.85, profitRate:  10.63, timestamp: Date.now() - 86400000*1 + 3600000 },
  { id:  7, orderId: 'ORD-20260312-002', strategy: 'S002', side: 'Warriors',    entryPrice: 0.390, exitPrice: 0.375, quantity: 700, amount: 273.00, profit: -27.30, profitRate:  -8.33, timestamp: Date.now() - 86400000*1 + 7200000 },
  { id:  8, orderId: 'ORD-20260312-003', strategy: 'S001', side: 'PSG',         entryPrice: 0.710, exitPrice: 0.740, quantity: 450, amount: 319.50, profit:  43.65, profitRate:  13.66, timestamp: Date.now() - 86400000*1 + 10800000 },
  { id:  9, orderId: 'ORD-20260312-004', strategy: 'R001', side: 'Bayern',      entryPrice: 0.550, exitPrice: 0.565, quantity: 600, amount: 330.00, profit:  16.50, profitRate:   5.00, timestamp: Date.now() - 43200000 },
  { id: 10, orderId: 'ORD-20260312-005', strategy: 'S002', side: 'Man City',    entryPrice: 0.620, exitPrice: 0.600, quantity: 300, amount: 186.00, profit: -18.60, profitRate:  -5.00, timestamp: Date.now() - 36000000 },
  { id: 11, orderId: 'ORD-20260313-001', strategy: 'S001', side: 'Knicks',      entryPrice: 0.450, exitPrice: 0.488, quantity: 800, amount: 360.00, profit:  57.60, profitRate:  16.00, timestamp: Date.now() - 21600000 },
  { id: 12, orderId: 'ORD-20260313-002', strategy: 'R001', side: 'Liverpool',   entryPrice: 0.680, exitPrice: 0.695, quantity: 250, amount: 170.00, profit:  10.50, profitRate:   6.18, timestamp: Date.now() - 14400000 },
  { id: 13, orderId: 'ORD-20260313-003', strategy: 'S002', side: 'Suns',        entryPrice: 0.530, exitPrice: 0.515, quantity: 500, amount: 265.00, profit: -19.87, profitRate:  -7.50, timestamp: Date.now() - 10800000 },
  { id: 14, orderId: 'ORD-20260313-004', strategy: 'R001', side: 'Juventus',    entryPrice: 0.420, exitPrice: 0.458, quantity: 900, amount: 378.00, profit:  68.04, profitRate:  18.00, timestamp: Date.now() - 7200000 },
  { id: 15, orderId: 'ORD-20260313-005', strategy: 'S001', side: 'Heat',        entryPrice: 0.600, exitPrice: 0.590, quantity: 300, amount: 180.00, profit:  -9.00, profitRate:  -3.33, timestamp: Date.now() - 3600000 },
];

const fmt = (n: number) =>
  n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const fmtK = (n: number) =>
  n >= 1000 ? `${(n / 1000).toFixed(1)}K` : n.toFixed(0);

const fmtExactMoney = (value: number | string | null | undefined): string => {
  if (value == null || value === '') return '0';
  const text = String(value).trim();
  if (!text || !Number.isFinite(Number(text))) return '0';
  if (!text.includes('.')) return text;
  return text.replace(/(\.\d*?[1-9])0+$/, '$1').replace(/\.0+$/, '');
};

const profitPct = (profit: number, base: number) =>
  base > 0 ? ((profit / base) * 100).toFixed(1) : '0.0';

const formatTradeTime = (timestamp: number): string => {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return '--';
  const d = new Date(timestamp);
  return `${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
};

const parseNumberOrDefault = (value: string, fallback: string) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : Number(fallback);
};

const nextTraderDisplayId = (accounts: TradingAccount[], mode: 'real' | 'simulation') => {
  const prefix = mode === 'real' ? 'R' : 'S';
  const maxNumber = accounts
    .filter(account => account.mode === mode)
    .map(account => displayTradingId(account.id, account.mode).match(/^[SR](\d+)$/)?.[1])
    .filter((value): value is string => Boolean(value))
    .map(value => Number(value))
    .reduce((max, value) => Math.max(max, value), 0);
  return `${prefix}${String(maxNumber + 1).padStart(3, '0')}`;
};

const SPORT_OPTIONS = ['足球'];

const FALLBACK_STRATEGY_OPTIONS = [
  { value: 'football_score_delay_trade', label: '足球-比分时差交易' },
  { value: 'football_winrate_gap_buy', label: '胜率差买入' },
];

const SPORT_KEY_BY_LABEL: Record<string, string> = {
  足球: 'football',
};

const SPORT_LABEL_BY_KEY: Record<string, string> = {
  football: '足球',
};

const STRATEGY_PARAM_DEFAULTS: Record<string, string> = {
  entry_spread_pct: '5',
  max_drawdown_pct: '2',
  winrate_gap_pct: '30',
  entry_before_minutes: '5',
  entry_after_minutes: '15',
};

const COMMON_PARAM_DEFAULTS = {
  maxPositions: '3',
  maxFundUsageRate: '80',
  maxSingleOrderPct: '20',
  maxAddCount: '2',
  maxAddFundPct: '10',
  stopLossDrawdown: '0.05',
};

function fallbackStrategyCatalogItem(strategyKey: string): StrategyCatalogItem | undefined {
  switch (strategyKey) {
    case 'football_score_delay_trade':
      return {
        key: strategyKey,
        display_name: '足球-比分时差交易',
        supported_sports: ['football'],
        params: [],
      };
    case 'football_winrate_gap_buy':
      return {
        key: strategyKey,
        display_name: '胜率差买入',
        supported_sports: ['football'],
        params: [
          {
            key: 'winrate_gap_pct',
            display_name: '胜率差阈值',
            value_type: 'number',
            required: true,
            min: 1,
            max: 100,
            decimals: 0,
            unit: '%',
            default: 30,
            description: '主队与客队 ask1 概率差达到该百分比才买入高胜率队。',
          },
          {
            key: 'entry_before_minutes',
            display_name: '开赛前窗口',
            value_type: 'number',
            required: true,
            min: 0,
            max: 60,
            decimals: 0,
            unit: '分钟',
            default: 5,
            description: '开赛前多少分钟内允许触发入场。',
          },
          {
            key: 'entry_after_minutes',
            display_name: '开赛后窗口',
            value_type: 'number',
            required: true,
            min: 0,
            max: 90,
            decimals: 0,
            unit: '分钟',
            default: 15,
            description: '0-0 时开赛后多少分钟内允许触发入场。',
          },
        ],
      };
    default:
      return undefined;
  }
}

function defaultStrategyParamValue(key: string, fallback?: number | string | null): string {
  if (fallback != null && `${fallback}`.trim().length > 0) {
    return `${fallback}`;
  }
  return STRATEGY_PARAM_DEFAULTS[key] ?? '';
}

function buildStrategyParamFormState(
  strategy: StrategyCatalogItem | undefined,
  currentValues: Record<string, number | string> = {},
): StrategyParamFormState {
  if (!strategy) {
    return {};
  }
  return Object.fromEntries(
    strategy.params.map((param) => [
      param.key,
      defaultStrategyParamValue(param.key, currentValues[param.key] ?? param.default ?? param.min ?? null),
    ]),
  );
}

function parseStrategyParamPayload(
  strategy: StrategyCatalogItem | undefined,
  values: StrategyParamFormState,
): Record<string, number> {
  if (!strategy) {
    return {};
  }
  return Object.fromEntries(
    strategy.params.flatMap((param) => {
      const numericValue = Number(values[param.key]);
      if (!Number.isFinite(numericValue)) {
        return [];
      }
      return [[param.key, numericValue]];
    }),
  );
}

const StrategyParamFields = ({
  strategy,
  values,
  onChange,
}: {
  strategy?: StrategyCatalogItem;
  values: StrategyParamFormState;
  onChange: (key: string, value: string) => void;
}) => {
  if (!strategy || strategy.params.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 pt-1">
        <div className="flex-1 h-px bg-gray-200" />
        <span className="text-[10px] text-gray-400 uppercase tracking-wider whitespace-nowrap">自定义参数</span>
        <div className="flex-1 h-px bg-gray-200" />
      </div>
      {strategy.params.map((param) => (
        <div key={param.key}>
          <label className="text-xs text-gray-500 block mb-1.5">
            {param.display_name || param.key}
            {param.unit ? ` (${param.unit})` : ''}
          </label>
          <input
            type="number"
            min={param.min ?? undefined}
            max={param.max ?? undefined}
            step={param.decimals != null ? (1 / Math.pow(10, param.decimals)).toString() : 'any'}
            value={values[param.key] ?? ''}
            onChange={e => onChange(param.key, e.target.value)}
            className="w-full border border-gray-200 bg-gray-50 rounded-md px-3 py-2 text-xs text-gray-900 focus:border-[#10b981] focus:outline-none"
            placeholder={defaultStrategyParamValue(param.key, param.min ?? null)}
          />
          {param.description && (
            <div className="text-[10px] text-gray-400 mt-1">{param.description}</div>
          )}
        </div>
      ))}
    </div>
  );
};

// ─── Shared Common Params Section ──────────────────────────────────────────
interface CommonParamsProps {
  maxPositions: string;
  maxFundUsageRate: string;
  maxSingleOrderPct: string;
  maxAddCount: string;
  maxAddFundPct: string;
  stopLossDrawdown: string;
  onChange: (field: string, value: string) => void;
}
const CommonParamsSection = ({
  maxPositions,
  maxFundUsageRate,
  maxSingleOrderPct,
  maxAddCount,
  maxAddFundPct,
  stopLossDrawdown,
  onChange,
}: CommonParamsProps) => (
  <div className="space-y-3">
    <div className="flex items-center gap-2 pt-1">
      <div className="flex-1 h-px bg-gray-200" />
      <span className="text-[10px] text-gray-400 uppercase tracking-wider whitespace-nowrap">通用参数</span>
      <div className="flex-1 h-px bg-gray-200" />
    </div>
    <div className="grid grid-cols-2 gap-3">
      <div>
        <label className="text-[10px] text-gray-500 block mb-1.5">最大持仓数量</label>
        <input
          type="number"
          min="1"
          value={maxPositions}
          onChange={e => onChange('maxPositions', e.target.value)}
          className="w-full border border-gray-200 bg-gray-50 rounded-md px-2.5 py-1.5 text-xs text-gray-900 focus:border-[#10b981] focus:outline-none"
          placeholder={COMMON_PARAM_DEFAULTS.maxPositions}
        />
      </div>
      <div>
        <label className="text-[10px] text-gray-500 block mb-1.5">资金利用率 %</label>
        <input
          type="number"
          min="1"
          max="100"
          value={maxFundUsageRate}
          onChange={e => onChange('maxFundUsageRate', e.target.value)}
          className="w-full border border-gray-200 bg-gray-50 rounded-md px-2.5 py-1.5 text-xs text-gray-900 focus:border-[#10b981] focus:outline-none"
          placeholder={COMMON_PARAM_DEFAULTS.maxFundUsageRate}
        />
      </div>
      <div>
        <label className="text-[10px] text-gray-500 block mb-1.5">单笔上限 %</label>
        <input
          type="number"
          min="1"
          max="100"
          value={maxSingleOrderPct}
          onChange={e => onChange('maxSingleOrderPct', e.target.value)}
          className="w-full border border-gray-200 bg-gray-50 rounded-md px-2.5 py-1.5 text-xs text-gray-900 focus:border-[#10b981] focus:outline-none"
          placeholder={COMMON_PARAM_DEFAULTS.maxSingleOrderPct}
        />
      </div>
      <div>
        <label className="text-[10px] text-gray-500 block mb-1.5">单场最多加仓次数</label>
        <input
          type="number"
          min="0"
          value={maxAddCount}
          onChange={e => onChange('maxAddCount', e.target.value)}
          className="w-full border border-gray-200 bg-gray-50 rounded-md px-2.5 py-1.5 text-xs text-gray-900 focus:border-[#10b981] focus:outline-none"
          placeholder={COMMON_PARAM_DEFAULTS.maxAddCount}
        />
      </div>
      <div>
        <label className="text-[10px] text-gray-500 block mb-1.5">单次加仓资金上限 %</label>
        <input
          type="number"
          min="1"
          max="100"
          value={maxAddFundPct}
          onChange={e => onChange('maxAddFundPct', e.target.value)}
          className="w-full border border-gray-200 bg-gray-50 rounded-md px-2.5 py-1.5 text-xs text-gray-900 focus:border-[#10b981] focus:outline-none"
          placeholder={COMMON_PARAM_DEFAULTS.maxAddFundPct}
        />
      </div>
      <div>
        <label className="text-[10px] text-gray-500 block mb-1.5">回撤多少自动卖出</label>
        <input
          type="number"
          min="0"
          max="1"
          step="0.01"
          value={stopLossDrawdown}
          onChange={e => onChange('stopLossDrawdown', e.target.value)}
          className="w-full border border-gray-200 bg-gray-50 rounded-md px-2.5 py-1.5 text-xs text-gray-900 focus:border-[#10b981] focus:outline-none"
          placeholder={COMMON_PARAM_DEFAULTS.stopLossDrawdown}
        />
      </div>
    </div>
  </div>
);

// ─── Component ─────────────────────────────────────────────────────────────
export const TradingPanel = () => {
  const { isSimulation, setSimulationMode } = useTrading();

  const [accounts, setAccounts] = useState<TradingAccount[]>([]);
  const [pmAccounts, setPmAccounts] = useState<PMAccountSummary[]>([]);
  const [positionRows, setPositionRows] = useState<any[]>([]);
  const [tradeRows, setTradeRows] = useState<any[]>([]);
  const [strategyCatalog, setStrategyCatalog] = useState<StrategyCatalogItem[]>([]);

  const loadTradingData = useCallback(async () => {
    const [accountsData, positionsData, tradesData] = await Promise.all([
      fetchAccounts(),
      fetchPositions(),
      fetchTrades(),
    ]);
    setAccounts(accountsData.map((row) => mapBackendAccountToTradingAccount(row)));
    setPositionRows(positionsData);
    setTradeRows(tradesData);
  }, []);

  useEffect(() => {
    let disposed = false;
    const load = async () => {
      try {
        await loadTradingData();
        if (disposed) return;
      } catch {
        if (!disposed) {
          setAccounts([]);
          setPositionRows([]);
          setTradeRows([]);
        }
      }
    };
    void load();
    const timer = setInterval(() => {
      void load();
    }, 5000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [loadTradingData]);

  useEffect(() => {
    let disposed = false;
    const loadPMAccounts = async () => {
      try {
        const rows = await fetchPmAccounts();
        if (!disposed) {
          setPmAccounts(rows);
        }
      } catch {
        if (!disposed) {
          setPmAccounts([]);
        }
      }
    };
    void loadPMAccounts();
    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    const loadCatalog = async () => {
      try {
        const next = await fetchStrategyCatalog();
        if (!disposed) {
          setStrategyCatalog(next);
        }
      } catch {
        if (!disposed) {
          setStrategyCatalog([]);
        }
      }
    };
    void loadCatalog();
    return () => {
      disposed = true;
    };
  }, []);

  // ── Dialog visibility ──
  const [showAddDialog, setShowAddDialog]       = useState(false);
  const [showEditDialog, setShowEditDialog]     = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [showStopDialog, setShowStopDialog]     = useState(false);
  const [showPositions, setShowPositions]       = useState(false);
  const [showTradeLogs, setShowTradeLogs]       = useState(false);
  const [currentAccountId, setCurrentAccountId]   = useState<string | null>(null);
  // Trade records search & pagination
  const [tradeSearch, setTradeSearch] = useState('');
  const [tradePage, setTradePage]     = useState(1);
  const TRADE_PAGE_SIZE = 8;

  // ── Add form ──
  const defaultAddForm = {
    strategy: FALLBACK_STRATEGY_OPTIONS[0].value,
    initialBalance: '10000',
    sports: ['足球'] as string[],
    pmAccountId: '',
    maxPositions: COMMON_PARAM_DEFAULTS.maxPositions,
    maxFundUsageRate: COMMON_PARAM_DEFAULTS.maxFundUsageRate,
    maxSingleOrderPct: COMMON_PARAM_DEFAULTS.maxSingleOrderPct,
    maxAddCount: COMMON_PARAM_DEFAULTS.maxAddCount,
    maxAddFundPct: COMMON_PARAM_DEFAULTS.maxAddFundPct,
    stopLossDrawdown: COMMON_PARAM_DEFAULTS.stopLossDrawdown,
    strategyParams: buildStrategyParamFormState(
      fallbackStrategyCatalogItem(FALLBACK_STRATEGY_OPTIONS[0].value),
    ),
  };
  const [addForm, setAddForm] = useState(defaultAddForm);

  // ── Edit form ──
  const [editForm, setEditForm] = useState({
    maxPositions: COMMON_PARAM_DEFAULTS.maxPositions,
    maxFundUsageRate: COMMON_PARAM_DEFAULTS.maxFundUsageRate,
    maxSingleOrderPct: COMMON_PARAM_DEFAULTS.maxSingleOrderPct,
    maxAddCount: COMMON_PARAM_DEFAULTS.maxAddCount,
    maxAddFundPct: COMMON_PARAM_DEFAULTS.maxAddFundPct,
    stopLossDrawdown: COMMON_PARAM_DEFAULTS.stopLossDrawdown,
    strategyParams: {} as StrategyParamFormState,
  });

  const strategyOptions = useMemo(
    () =>
      strategyCatalog.length > 0
        ? strategyCatalog.map((item) => ({ value: item.key, label: item.display_name }))
        : FALLBACK_STRATEGY_OPTIONS,
    [strategyCatalog]
  );

  const selectedAddStrategy = useMemo(
    () => strategyCatalog.find((item) => item.key === addForm.strategy) ?? fallbackStrategyCatalogItem(addForm.strategy),
    [addForm.strategy, strategyCatalog]
  );

  const currentAccount    = accounts.find(a => a.id === currentAccountId);
  const currentAccountDisplayId = currentAccount
    ? displayTradingId(currentAccount.id, currentAccount.mode)
    : currentAccountId || '';

  const selectedEditStrategy = useMemo(
    () =>
      currentAccount
        ? strategyCatalog.find((item) => item.key === currentAccount.strategyKey) ??
          fallbackStrategyCatalogItem(currentAccount.strategyKey)
        : undefined,
    [currentAccount, strategyCatalog]
  );

  const allowedSportLabels = useMemo(() => {
    const keys =
      selectedAddStrategy?.supported_sports.length
        ? selectedAddStrategy.supported_sports
        : SPORT_OPTIONS.map((item) => SPORT_KEY_BY_LABEL[item]).filter(Boolean);
    return keys
      .map((key) => SPORT_LABEL_BY_KEY[key] ?? key)
      .filter((value): value is string => Boolean(value));
  }, [selectedAddStrategy]);

  useEffect(() => {
    setAddForm((prev) => {
      const nextStrategy =
        strategyOptions.some((option) => option.value === prev.strategy)
          ? prev.strategy
          : strategyOptions[0]?.value ?? prev.strategy;
      const nextSports = prev.sports.filter((sport) => allowedSportLabels.includes(sport));
      const normalizedSports = nextSports.length > 0 ? nextSports : allowedSportLabels;
      const nextStrategyDef =
        strategyCatalog.find((item) => item.key === nextStrategy) ?? fallbackStrategyCatalogItem(nextStrategy);
      const nextStrategyParams = buildStrategyParamFormState(nextStrategyDef, prev.strategyParams);
      if (
        nextStrategy === prev.strategy &&
        normalizedSports.join('|') === prev.sports.join('|') &&
        JSON.stringify(nextStrategyParams) === JSON.stringify(prev.strategyParams)
      ) {
        return prev;
      }
      return {
        ...prev,
        strategy: nextStrategy,
        sports: normalizedSports,
        strategyParams: nextStrategyParams,
      };
    });
  }, [allowedSportLabels, strategyCatalog, strategyOptions]);

  const viewPositions: Position[] = useMemo(
    () =>
      positionRows.map((pos: any, index: number) => mapBackendPositionRow(pos, index)),
    [positionRows]
  );

	  const viewTrades: TradeRecord[] = useMemo(
	    () =>
	      tradeRows.map((rec: any, index: number) => mapBackendTradeRow(rec, index)),
	    [tradeRows]
	  );

  // ── Handlers ──
  const handleAddAccount = async () => {
    if (!isSimulation && !addForm.pmAccountId) {
      return;
    }
    const initialBalance = resolveTraderInitialBalance(
      isSimulation ? 'simulation' : 'real',
      addForm.initialBalance,
      selectedPMAccount,
    );
    const affectSports = addForm.sports
      .map((sport) => SPORT_KEY_BY_LABEL[sport] ?? '')
      .filter((sport): sport is string => sport.length > 0);
    const strategyParams: Record<string, number> = {
      initial_balance: initialBalance,
      ...parseStrategyParamPayload(selectedAddStrategy, addForm.strategyParams),
    };
    await createTrading({
      strategy_name: addForm.strategy,
      strategy_params: {
        ...strategyParams,
        ...(isSimulation ? {} : { real_dry_run: true }),
        risk: {
          max_positions: parseNumberOrDefault(addForm.maxPositions, COMMON_PARAM_DEFAULTS.maxPositions),
          max_fund_usage_pct: parseNumberOrDefault(addForm.maxFundUsageRate, COMMON_PARAM_DEFAULTS.maxFundUsageRate),
          max_single_order_pct: parseNumberOrDefault(addForm.maxSingleOrderPct, COMMON_PARAM_DEFAULTS.maxSingleOrderPct),
          max_add_count: parseNumberOrDefault(addForm.maxAddCount, COMMON_PARAM_DEFAULTS.maxAddCount),
          max_add_fund_pct: parseNumberOrDefault(addForm.maxAddFundPct, COMMON_PARAM_DEFAULTS.maxAddFundPct),
          stop_loss_drawdown: parseNumberOrDefault(addForm.stopLossDrawdown, COMMON_PARAM_DEFAULTS.stopLossDrawdown),
        },
      },
      affect_sports: affectSports,
      mode: isSimulation ? 'simulation' : 'real',
      account_alias: isSimulation ? undefined : addForm.pmAccountId,
    }).then((created) => startTradingInstance(created.trading_id));
    await loadTradingData();
    setShowAddDialog(false);
  };

  const handleUpdateAccount = async () => {
    if (!currentAccountId || !currentAccount) {
      return;
    }
    const currentStrategy =
      strategyCatalog.find((item) => item.key === currentAccount.strategyKey) ??
      fallbackStrategyCatalogItem(currentAccount.strategyKey);
    const nextStrategyParams: Record<string, number> = parseStrategyParamPayload(
      currentStrategy,
      editForm.strategyParams,
    );
    await updateTradingInstance(currentAccountId, {
      strategy_params: {
        ...nextStrategyParams,
        risk: {
          max_positions: parseNumberOrDefault(editForm.maxPositions, COMMON_PARAM_DEFAULTS.maxPositions),
          max_fund_usage_pct: parseNumberOrDefault(editForm.maxFundUsageRate, COMMON_PARAM_DEFAULTS.maxFundUsageRate),
          max_single_order_pct: parseNumberOrDefault(editForm.maxSingleOrderPct, COMMON_PARAM_DEFAULTS.maxSingleOrderPct),
          max_add_count: parseNumberOrDefault(editForm.maxAddCount, COMMON_PARAM_DEFAULTS.maxAddCount),
          max_add_fund_pct: parseNumberOrDefault(editForm.maxAddFundPct, COMMON_PARAM_DEFAULTS.maxAddFundPct),
          stop_loss_drawdown: parseNumberOrDefault(editForm.stopLossDrawdown, COMMON_PARAM_DEFAULTS.stopLossDrawdown),
        },
      },
    });
    await loadTradingData();
    setShowEditDialog(false);
    setCurrentAccountId(null);
  };

  const handleDeleteAccount = async () => {
    if (!currentAccountId) return;
    const deletingId = currentAccountId;
    setAccounts((prev) => prev.filter((account) => account.id !== deletingId));
    setPositionRows((prev) => prev.filter((row: any) => row.trading_id !== deletingId && row.trader_id !== deletingId));
    setTradeRows((prev) => prev.filter((row: any) => row.trading_id !== deletingId && row.trader_id !== deletingId));
    await deleteTradingInstance(deletingId);
    await loadTradingData();
    setShowDeleteDialog(false);
    setCurrentAccountId(null);
  };

  const handleToggleAccount = async (id: string) => {
    await startTradingInstance(id);
    await loadTradingData();
  };

  const handleOpenAddDialog = () => {
    setAddForm({
      ...defaultAddForm,
      strategyParams: buildStrategyParamFormState(
        strategyCatalog.find((item) => item.key === defaultAddForm.strategy) ??
          fallbackStrategyCatalogItem(defaultAddForm.strategy),
      ),
    });
    setShowAddDialog(true);
  };

  const handleOpenEditDialog = (account: TradingAccount) => {
    const currentStrategy =
      strategyCatalog.find((item) => item.key === account.strategyKey) ??
      fallbackStrategyCatalogItem(account.strategyKey);
    setCurrentAccountId(account.id);
    setEditForm({
      maxPositions: account.maxPositions.toString(),
      maxFundUsageRate: account.maxFundUsageRate.toString(),
      maxSingleOrderPct: account.maxSingleOrderPct.toString(),
      maxAddCount: account.maxAddCount.toString(),
      maxAddFundPct: account.maxAddFundPct.toString(),
      stopLossDrawdown: account.stopLossDrawdown.toString(),
      strategyParams: buildStrategyParamFormState(currentStrategy, account.strategyConfig),
    });
    setShowEditDialog(true);
  };

  const handleConfirmStop = async () => {
    if (currentAccountId) {
      await stopTradingInstance(currentAccountId);
      await loadTradingData();
    }
    setShowStopDialog(false);
    setCurrentAccountId(null);
  };

  const filteredAccounts = accounts.filter(a =>
    isSimulation ? a.mode === 'simulation' : a.mode === 'real'
  );

  const getProfitColor = profitColorClass;

  const toggleSportAdd = (sport: string) => {
    setAddForm(prev => ({
      ...prev,
      sports: prev.sports.includes(sport)
        ? prev.sports.filter(s => s !== sport)
        : [...prev.sports, sport],
    }));
  };

  const selectedPMAccount = pmAccounts.find(p => p.id === addForm.pmAccountId);

  return (
    <Tooltip.Provider>
      <div className="flex flex-col h-full bg-gray-50 text-sm overflow-hidden">

        {/* ── Header ───────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between p-3 border-b border-l border-gray-200 bg-white">
          <h2 className="text-gray-900 font-bold text-base">交易</h2>
          <div className="flex items-center gap-1">
            <div className="flex bg-gray-100 rounded-md p-0.5">
              <button
                onClick={() => setSimulationMode(false)}
                className={clsx(
                  'px-2.5 py-1 rounded text-xs font-medium transition-all cursor-pointer',
                  !isSimulation ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
                )}
              >实盘</button>
              <button
                onClick={() => setSimulationMode(true)}
                className={clsx(
                  'px-2.5 py-1 rounded text-xs font-medium transition-all cursor-pointer',
                  isSimulation ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
                )}
              >模拟</button>
            </div>
            <button
              onClick={handleOpenAddDialog}
              className="p-1.5 rounded-md transition-colors text-gray-500 cursor-pointer hover:bg-gray-100 hover:text-[#10b981]"
              title="添加交易账户"
            >
              <Plus size={16} />
            </button>
          </div>
        </div>

        {/* ── Account Cards ─────────────────────────────────────────────── */}
        <div className="flex-1 overflow-y-auto custom-scrollbar pl-2 pr-3 pt-2 pb-2 space-y-2">
          <div className="space-y-2">
            {filteredAccounts.map(account => (
              <div key={account.id} className="bg-white border border-gray-200 rounded-md p-3">

                {/* Card header */}
                <div className="flex justify-between items-center mb-3">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <span className={clsx(
                      'px-2 py-0.5 rounded text-xs font-bold whitespace-nowrap',
                      account.mode === 'real'
                        ? 'bg-green-50 text-green-600 border border-green-200'
                        : 'bg-blue-50 text-blue-600 border border-blue-200'
                    )}>
                      {displayTradingId(account.id, account.mode)}
                    </span>

                    <Tooltip.Root>
                      <Tooltip.Trigger
                        onClick={() => handleOpenEditDialog(account)}
                        className="text-xs text-gray-700 font-medium truncate cursor-pointer hover:text-gray-900 transition-colors flex-1 text-left bg-transparent border-none p-0 min-w-0"
                      >
                        {account.strategyName}
                      </Tooltip.Trigger>
                      <Tooltip.Portal>
                        <Tooltip.Content
                          className="bg-gray-900 text-white text-xs px-2 py-1 rounded shadow-lg max-w-xs z-50"
                          sideOffset={5}
                        >
                          {account.strategyName}
                          <Tooltip.Arrow className="fill-gray-900" />
                        </Tooltip.Content>
                      </Tooltip.Portal>
                    </Tooltip.Root>
                  </div>

                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      onClick={() => { setCurrentAccountId(account.id); setShowDeleteDialog(true); }}
                      className="transition-colors rounded-full p-1.5 cursor-pointer text-gray-400 hover:bg-red-50 hover:text-red-500"
                      title="删除交易员"
                    >
                      <Trash2 size={15} />
                    </button>
                    <button
                      onClick={() => {
                        if (account.isRunning) {
                          setCurrentAccountId(account.id);
                          setShowStopDialog(true);
                        } else {
                          handleToggleAccount(account.id);
                        }
                      }}
                      className={clsx(
                        'transition-transform active:scale-95 rounded-full p-1.5 cursor-pointer shadow-sm border border-gray-100',
                        account.isRunning
                          ? 'bg-red-50 text-red-500 hover:bg-red-100'
                          : 'bg-[#10b981]/10 text-[#10b981] hover:bg-[#10b981]/20'
                      )}
                    >
                      {account.isRunning
                        ? <Pause fill="currentColor" size={16} />
                        : <Play  fill="currentColor" size={16} className="ml-0.5" />}
                    </button>
                  </div>
                </div>

                {/* Stats */}
                <div className="space-y-1.5 mb-3">
                  {/* 总资产 with available cash */}
                  <div className="flex justify-between items-center text-xs">
                    <span className="text-gray-500">总资产</span>
                    <span className="text-gray-900 font-bold font-mono">
                      ${fmt(account.totalAssets)}
                      <span className="text-gray-400 font-normal"> (${fmt(account.availableCash)})</span>
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-xs">
                    <span className="text-gray-500">总市值</span>
                    <span className="text-gray-900 font-mono">${fmt(account.marketValue)}</span>
                  </div>
                  <div className="flex justify-between items-center text-xs">
                    <span className="text-gray-500">今日收益</span>
                    <span className={clsx('font-bold font-mono', getProfitColor(account.todayProfit))}>
                      {formatSignedUsd(account.todayProfit)}
                      {' '}({profitPct(account.todayProfit, account.initialBalance)}%)
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-xs">
                    <span className="text-gray-500">总收益</span>
                    <span className={clsx('font-bold font-mono', getProfitColor(account.totalProfit))}>
                      {formatSignedUsd(account.totalProfit)}
                      {' '}({profitPct(account.totalProfit, account.initialBalance)}%)
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-xs">
                    <span className="text-gray-500">胜率</span>
                    <span className="text-gray-900 font-bold">{account.winRate}%</span>
                  </div>
                </div>

                {/* Action buttons */}
                <div className="flex gap-2 pt-2 border-t border-gray-100">
                  <button
                    onClick={() => { setCurrentAccountId(account.id); setShowPositions(true); }}
                    className="flex-1 bg-gray-50 hover:bg-gray-100 text-gray-700 py-1.5 rounded text-xs font-medium transition-colors cursor-pointer border border-gray-200"
                  >
                    持仓({account.positionCount})
                  </button>
                  <button
                    onClick={() => { setCurrentAccountId(account.id); setShowTradeLogs(true); }}
                    className="flex-1 bg-gray-50 hover:bg-gray-100 text-gray-700 py-1.5 rounded text-xs font-medium transition-colors cursor-pointer border border-gray-200"
                  >
                    交易记录
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ════════════════════════════════════════════════════════════════
            ADD DIALOG — Real mode
        ════════════════════════════════════════════════════════════════ */}
        <Dialog.Root open={showAddDialog && !isSimulation} onOpenChange={v => !v && setShowAddDialog(false)}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 bg-gray-900/50 z-50 backdrop-blur-sm" />
            <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white border border-gray-200 rounded-lg shadow-2xl p-5 w-[460px] max-h-[90vh] overflow-y-auto z-50 custom-scrollbar">
              <div className="flex justify-between items-center mb-5">
                <Dialog.Title className="text-base font-bold text-gray-900">
                  添加实盘交易账户
                </Dialog.Title>
                <Dialog.Close className="text-gray-400 hover:text-gray-600 cursor-pointer">
                  <X size={18} />
                </Dialog.Close>
              </div>
              <Dialog.Description className="sr-only">配置新的实盘交易账户</Dialog.Description>

              <div className="space-y-4">
                {/* 交易员编号 */}
                <div>
                  <label className="text-xs text-gray-500 block mb-1.5">交易员编号</label>
                  <input
                    type="text"
                    value={nextTraderDisplayId(accounts, 'real')}
                    disabled
                    className="w-full border border-gray-200 bg-gray-100 rounded-md px-3 py-2 text-xs text-gray-500 cursor-not-allowed"
                  />
                </div>

                {/* PM账户选择 */}
                <div>
                  <label className="text-xs text-gray-500 block mb-1.5">PM 账户</label>
                  <div className="relative">
                    <select
                      value={addForm.pmAccountId}
                      onChange={e => setAddForm(p => ({ ...p, pmAccountId: e.target.value }))}
                      className="w-full border border-gray-200 bg-white rounded-md px-3 py-2 text-xs text-gray-900 focus:border-[#10b981] focus:outline-none cursor-pointer appearance-none pr-8"
                    >
                      <option value="">{pmAccounts.length > 0 ? '— 请选择账户 —' : '未配置 PM_ACCOUNTS_JSON'}</option>
                      {pmAccounts.map(pm => (
                        <option key={pm.id} value={pm.id}>{pm.name}</option>
                      ))}
                    </select>
                    <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
                  </div>
                </div>

                {/* PM账户资金信息 */}
                {selectedPMAccount && (
                  <div className="bg-gray-50 border border-gray-200 rounded-md p-3 space-y-2">
                    <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-1">账户资金概览</div>
                    <div className="grid grid-cols-3 gap-2">
                      <div className="text-center bg-white border border-gray-100 rounded-md py-2 px-1">
                        <div className="flex justify-center mb-1"><Wallet size={12} className="text-gray-400" /></div>
                        <div className="text-[10px] text-gray-400 mb-0.5">总资金</div>
                        <div className="text-xs font-bold text-gray-800 font-mono">${fmtExactMoney(selectedPMAccount.total_funds)}</div>
                      </div>
                      <div className="text-center bg-white border border-gray-100 rounded-md py-2 px-1">
                        <div className="flex justify-center mb-1"><TrendingUp size={12} className="text-blue-400" /></div>
                        <div className="text-[10px] text-gray-400 mb-0.5">持仓资金</div>
                        <div className="text-xs font-bold text-blue-600 font-mono">${fmtExactMoney(selectedPMAccount.position_funds)}</div>
                      </div>
                      <div className="text-center bg-white border border-gray-100 rounded-md py-2 px-1">
                        <div className="flex justify-center mb-1"><DollarSign size={12} className="text-[#10b981]" /></div>
                        <div className="text-[10px] text-gray-400 mb-0.5">可用资金</div>
                        <div className="text-xs font-bold text-[#10b981] font-mono">${fmtExactMoney(selectedPMAccount.available_funds)}</div>
                      </div>
                    </div>
                    {selectedPMAccount.balance_error && (
                      <div className="text-[11px] text-red-500 leading-relaxed">
                        PM 资金查询失败：{selectedPMAccount.balance_error}
                      </div>
                    )}
                  </div>
                )}

                {/* 策略选择 */}
                <div>
                  <label className="text-xs text-gray-500 block mb-1.5">策略选择</label>
                  <div className="relative">
                    <select
                      value={addForm.strategy}
                      onChange={e => setAddForm(p => ({ ...p, strategy: e.target.value }))}
                      className="w-full border border-gray-200 bg-white rounded-md px-3 py-2 text-xs text-gray-900 focus:border-[#10b981] focus:outline-none cursor-pointer appearance-none pr-8"
                    >
                      {strategyOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                    <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
                  </div>
                </div>

                <StrategyParamFields
                  strategy={selectedAddStrategy}
                  values={addForm.strategyParams}
                  onChange={(key, value) => setAddForm(p => ({
                    ...p,
                    strategyParams: {
                      ...p.strategyParams,
                      [key]: value,
                    },
                  }))}
                />

                {/* 适用比赛 */}
                <div>
                  <label className="text-xs text-gray-500 block mb-1.5">适用比赛</label>
                  <div className="flex flex-wrap gap-2">
                    {allowedSportLabels.map(sport => (
                      <button
                        key={sport}
                        onClick={() => toggleSportAdd(sport)}
                        className={clsx(
                          'px-3 py-1.5 rounded-md text-xs font-medium transition-all cursor-pointer border',
                          addForm.sports.includes(sport)
                            ? 'bg-[#10b981] text-white border-[#10b981]'
                            : 'bg-white text-gray-600 border-gray-200 hover:border-[#10b981]'
                        )}
                      >{sport}</button>
                    ))}
                  </div>
                </div>

                <CommonParamsSection
                  maxPositions={addForm.maxPositions}
                  maxFundUsageRate={addForm.maxFundUsageRate}
                  maxSingleOrderPct={addForm.maxSingleOrderPct}
                  maxAddCount={addForm.maxAddCount}
                  maxAddFundPct={addForm.maxAddFundPct}
                  stopLossDrawdown={addForm.stopLossDrawdown}
                  onChange={(field, value) => setAddForm(p => ({ ...p, [field]: value }))}
                />

                <div className="flex gap-2 pt-2">
                  <button
                    onClick={() => setShowAddDialog(false)}
                    className="flex-1 bg-gray-100 hover:bg-gray-200 text-gray-600 py-2 rounded-md text-xs transition-colors cursor-pointer"
                  >取消</button>
                  <button
                    onClick={handleAddAccount}
                    disabled={!addForm.pmAccountId}
                    className={clsx(
                      'flex-1 py-2 rounded-md text-xs transition-colors cursor-pointer text-white',
                      addForm.pmAccountId
                        ? 'bg-[#10b981] hover:bg-[#0ea571]'
                        : 'bg-gray-300 cursor-not-allowed'
                    )}
                  >添加</button>
                </div>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>

        {/* ════════════════════════════════════════════════════════════════
            ADD DIALOG — Simulation mode
        ════════════════════════════════════════════════════════════════ */}
        <Dialog.Root open={showAddDialog && isSimulation} onOpenChange={v => !v && setShowAddDialog(false)}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 bg-gray-900/50 z-50 backdrop-blur-sm" />
            <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white border border-gray-200 rounded-lg shadow-2xl p-5 w-[440px] max-h-[90vh] overflow-y-auto z-50 custom-scrollbar">
              <div className="flex justify-between items-center mb-5">
                <Dialog.Title className="text-base font-bold text-gray-900">
                  添加模拟交易账户
                </Dialog.Title>
                <Dialog.Close className="text-gray-400 hover:text-gray-600 cursor-pointer">
                  <X size={18} />
                </Dialog.Close>
              </div>
              <Dialog.Description className="sr-only">配置新的模拟交易账户</Dialog.Description>

              <div className="space-y-4">
                {/* 交易员编号 */}
                <div>
                  <label className="text-xs text-gray-500 block mb-1.5">交易员编号</label>
                  <input
                    type="text"
                    value={nextTraderDisplayId(accounts, 'simulation')}
                    disabled
                    className="w-full border border-gray-200 bg-gray-100 rounded-md px-3 py-2 text-xs text-gray-500 cursor-not-allowed"
                  />
                </div>

                {/* 初始资金 */}
                <div>
                  <label className="text-xs text-gray-500 block mb-1.5">初始资金 ($)</label>
                  <input
                    type="number"
                    value={addForm.initialBalance}
                    onChange={e => setAddForm(p => ({ ...p, initialBalance: e.target.value }))}
                    className="w-full border border-gray-200 bg-gray-50 rounded-md px-3 py-2 text-xs text-gray-900 focus:border-[#10b981] focus:outline-none"
                    placeholder="100000"
                  />
                </div>

                {/* 策略选择 */}
                <div>
                  <label className="text-xs text-gray-500 block mb-1.5">策略选择</label>
                  <div className="relative">
                    <select
                      value={addForm.strategy}
                      onChange={e => setAddForm(p => ({ ...p, strategy: e.target.value }))}
                      className="w-full border border-gray-200 bg-white rounded-md px-3 py-2 text-xs text-gray-900 focus:border-[#10b981] focus:outline-none cursor-pointer appearance-none pr-8"
                    >
                      {strategyOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                    <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
                  </div>
                </div>

                <StrategyParamFields
                  strategy={selectedAddStrategy}
                  values={addForm.strategyParams}
                  onChange={(key, value) => setAddForm(p => ({
                    ...p,
                    strategyParams: {
                      ...p.strategyParams,
                      [key]: value,
                    },
                  }))}
                />

                {/* 适用比赛 */}
                <div>
                  <label className="text-xs text-gray-500 block mb-1.5">适用比赛</label>
                  <div className="flex flex-wrap gap-2">
                    {allowedSportLabels.map(sport => (
                      <button
                        key={sport}
                        onClick={() => toggleSportAdd(sport)}
                        className={clsx(
                          'px-3 py-1.5 rounded-md text-xs font-medium transition-all cursor-pointer border',
                          addForm.sports.includes(sport)
                            ? 'bg-[#10b981] text-white border-[#10b981]'
                            : 'bg-white text-gray-600 border-gray-200 hover:border-[#10b981]'
                        )}
                      >{sport}</button>
                    ))}
                  </div>
                </div>

                <CommonParamsSection
                  maxPositions={addForm.maxPositions}
                  maxFundUsageRate={addForm.maxFundUsageRate}
                  maxSingleOrderPct={addForm.maxSingleOrderPct}
                  maxAddCount={addForm.maxAddCount}
                  maxAddFundPct={addForm.maxAddFundPct}
                  stopLossDrawdown={addForm.stopLossDrawdown}
                  onChange={(field, value) => setAddForm(p => ({ ...p, [field]: value }))}
                />

                <div className="flex gap-2 pt-2">
                  <button
                    onClick={() => setShowAddDialog(false)}
                    className="flex-1 bg-gray-100 hover:bg-gray-200 text-gray-600 py-2 rounded-md text-xs transition-colors cursor-pointer"
                  >取消</button>
                  <button
                    onClick={handleAddAccount}
                    className="flex-1 bg-[#10b981] hover:bg-[#0ea571] text-white py-2 rounded-md text-xs transition-colors cursor-pointer"
                  >添加</button>
                </div>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>

        {/* ════════════════════════════════════════════════════════════════
            EDIT DIALOG
        ════════════════════════════════════════════════════════════════ */}
        <Dialog.Root open={showEditDialog} onOpenChange={setShowEditDialog}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 bg-gray-900/50 z-50 backdrop-blur-sm" />
            <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white border border-gray-200 rounded-lg shadow-2xl p-5 w-[440px] max-h-[90vh] overflow-y-auto z-50 custom-scrollbar">
              <div className="flex justify-between items-center mb-5">
                <Dialog.Title className="text-base font-bold text-gray-900">策略配置 — {currentAccountDisplayId}</Dialog.Title>
                <Dialog.Close className="text-gray-400 hover:text-gray-600 cursor-pointer"><X size={18} /></Dialog.Close>
              </div>
              <Dialog.Description className="sr-only">修改交易账户的策略参数配置</Dialog.Description>

              <div className="space-y-4">
                <div>
                  <label className="text-xs text-gray-500 block mb-1.5">交易员编号</label>
                  <input type="text" value={currentAccountDisplayId} disabled
                    className="w-full border border-gray-200 bg-gray-100 rounded-md px-3 py-2 text-xs text-gray-500 cursor-not-allowed" />
                </div>

                {/* Show PM account info for real accounts */}
                {currentAccount?.mode === 'real' && currentAccount.pmAccountId && (() => {
                  const pm = pmAccounts.find(p => p.id === currentAccount.pmAccountId);
                  if (!pm) return null;
                  return (
                    <div>
                      <label className="text-xs text-gray-500 block mb-1.5">PM 账户</label>
                      <div className="w-full border border-gray-200 bg-gray-100 rounded-md px-3 py-2 text-xs text-gray-500">{pm.name}</div>
                      <div className="bg-gray-50 border border-gray-200 rounded-md p-3 mt-2 grid grid-cols-3 gap-2">
                        <div className="text-center">
                          <div className="text-[10px] text-gray-400">总资金</div>
                          <div className="text-xs font-bold text-gray-700 font-mono">${fmtExactMoney(pm.total_funds)}</div>
                        </div>
                        <div className="text-center">
                          <div className="text-[10px] text-gray-400">持仓资金</div>
                          <div className="text-xs font-bold text-blue-600 font-mono">${fmtExactMoney(pm.position_funds)}</div>
                        </div>
                        <div className="text-center">
                          <div className="text-[10px] text-gray-400">可用资金</div>
                          <div className="text-xs font-bold text-[#10b981] font-mono">${fmtExactMoney(pm.available_funds)}</div>
                        </div>
                      </div>
                      {pm.balance_error && (
                        <div className="text-[11px] text-red-500 mt-2 leading-relaxed">
                          PM 资金查询失败：{pm.balance_error}
                        </div>
                      )}
                    </div>
                  );
                })()}

                <div>
                  <label className="text-xs text-gray-500 block mb-1.5">策略</label>
                  <input type="text" value={currentAccount?.strategyName || ''} disabled
                    className="w-full border border-gray-200 bg-gray-100 rounded-md px-3 py-2 text-xs text-gray-500 cursor-not-allowed" />
                </div>

                <StrategyParamFields
                  strategy={selectedEditStrategy}
                  values={editForm.strategyParams}
                  onChange={(key, value) => setEditForm(p => ({
                    ...p,
                    strategyParams: {
                      ...p.strategyParams,
                      [key]: value,
                    },
                  }))}
                />

                <div>
                  <label className="text-xs text-gray-500 block mb-1.5">初始资金 ($)</label>
                  <input type="text" value={`$${fmt(currentAccount?.initialBalance ?? 0)}`} disabled
                    className="w-full border border-gray-200 bg-gray-100 rounded-md px-3 py-2 text-xs text-gray-500 cursor-not-allowed" />
                </div>

                <CommonParamsSection
                  maxPositions={editForm.maxPositions}
                  maxFundUsageRate={editForm.maxFundUsageRate}
                  maxSingleOrderPct={editForm.maxSingleOrderPct}
                  maxAddCount={editForm.maxAddCount}
                  maxAddFundPct={editForm.maxAddFundPct}
                  stopLossDrawdown={editForm.stopLossDrawdown}
                  onChange={(field, value) => setEditForm(p => ({ ...p, [field]: value }))}
                />

                <div className="flex gap-2 pt-2">
                  <button onClick={() => setShowEditDialog(false)}
                    className="flex-1 bg-gray-100 hover:bg-gray-200 text-gray-600 py-2 rounded-md text-xs transition-colors cursor-pointer">取消</button>
                  <button onClick={handleUpdateAccount}
                    className="flex-1 bg-[#10b981] hover:bg-[#0ea571] text-white py-2 rounded-md text-xs transition-colors cursor-pointer">保存</button>
                </div>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>

        {/* ── Delete Dialog ─────────────────────────────────────────────── */}
        <Dialog.Root open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 bg-gray-900/50 z-50 backdrop-blur-sm" />
            <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white border border-gray-200 rounded-lg shadow-2xl p-5 w-[380px] z-50">
              <div className="flex justify-between items-center mb-5">
                <Dialog.Title className="text-base font-bold text-gray-900">确认删除</Dialog.Title>
                <Dialog.Close className="text-gray-400 hover:text-gray-600 cursor-pointer"><X size={18} /></Dialog.Close>
              </div>
              <Dialog.Description className="sr-only">确认删除交易账户操作</Dialog.Description>
              <p className="text-sm text-gray-600 mb-6">
                确定要删除交易账户 <span className="font-bold text-gray-900">{currentAccountDisplayId}</span> 吗？此操作无法撤销。
              </p>
              <div className="flex gap-2">
                <button onClick={() => setShowDeleteDialog(false)}
                  className="flex-1 bg-gray-100 hover:bg-gray-200 text-gray-600 py-2 rounded-md text-xs transition-colors cursor-pointer">取消</button>
                <button onClick={handleDeleteAccount}
                  className="flex-1 bg-red-500 hover:bg-red-600 text-white py-2 rounded-md text-xs transition-colors cursor-pointer">确认删除</button>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>

        {/* ── Stop Dialog ────────────────────────────────────────���──────── */}
        <Dialog.Root open={showStopDialog} onOpenChange={setShowStopDialog}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 bg-gray-900/50 z-50 backdrop-blur-sm" />
            <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white border border-gray-200 rounded-lg shadow-2xl p-5 w-[380px] z-50">
              <div className="flex justify-between items-center mb-5">
                <Dialog.Title className="text-base font-bold text-gray-900">确认停止</Dialog.Title>
                <Dialog.Close className="text-gray-400 hover:text-gray-600 cursor-pointer"><X size={18} /></Dialog.Close>
              </div>
              <Dialog.Description className="sr-only">确认停止交易账户运行</Dialog.Description>
              <p className="text-sm text-gray-600 mb-6">
                确定要停止交易账户 <span className="font-bold text-gray-900">{currentAccountDisplayId}</span> 吗？停止后将不再自动交易。
              </p>
              <div className="flex gap-2">
                <button onClick={() => setShowStopDialog(false)}
                  className="flex-1 bg-gray-100 hover:bg-gray-200 text-gray-600 py-2 rounded-md text-xs transition-colors cursor-pointer">取消</button>
                <button onClick={handleConfirmStop}
                  className="flex-1 bg-red-500 hover:bg-red-600 text-white py-2 rounded-md text-xs transition-colors cursor-pointer">确认停止</button>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>

        {/* ── Positions Dialog ──────────────────────────────────────────── */}
        <Dialog.Root open={showPositions} onOpenChange={setShowPositions}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 bg-gray-900/50 z-50 backdrop-blur-sm" />
            <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white border border-gray-200 rounded-lg shadow-2xl w-[1120px] max-w-[94vw] h-[78vh] z-50 flex flex-col">
              <div className="flex justify-between items-center px-5 pt-5 pb-3 border-b border-gray-100 flex-shrink-0">
                <div>
                  <Dialog.Title className="text-base font-bold text-gray-900">持仓详情 — {currentAccountDisplayId}</Dialog.Title>
                  <p className="text-[11px] text-gray-400 mt-0.5">{viewPositions.length} 个持仓中</p>
                </div>
                <Dialog.Close className="text-gray-400 hover:text-gray-600 cursor-pointer"><X size={18} /></Dialog.Close>
              </div>
              <Dialog.Description className="sr-only">查看交易账户的当前持仓信息</Dialog.Description>
              <div className="flex-1 overflow-auto custom-scrollbar">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-gray-50 border-b border-gray-200">
                    <tr className="text-gray-400">
                      <th className="px-4 py-2.5 text-left font-medium">订单编号</th>
                      <th className="px-3 py-2.5 text-left font-medium">比赛</th>
                      <th className="px-3 py-2.5 text-left font-medium">队伍</th>
                      <th className="px-3 py-2.5 text-right font-medium">买入价</th>
                      <th className="px-3 py-2.5 text-right font-medium">现价</th>
                      <th className="px-3 py-2.5 text-right font-medium">份额</th>
                      <th className="px-3 py-2.5 text-right font-medium">投入 $</th>
                      <th className="px-4 py-2.5 text-right font-medium">浮动盈亏</th>
                    </tr>
                  </thead>
                  <tbody>
                    {viewPositions.map(pos => {
                      const isProfit = pos.profit > 0;
                      const profitColor = profitColorClass(pos.profit);
                      const profitPrefix = pos.profit > 0 ? '+' : pos.profit < 0 ? '-' : '';
                      return (
                        <tr key={pos.id} className="border-b border-gray-50 hover:bg-gray-50 transition-colors">
                          <td className="px-4 py-2.5">
                            <span className="font-mono text-[11px] text-gray-500">{pos.orderId}</span>
                          </td>
                          <td className="px-3 py-2.5">
                            <span className="text-gray-700 font-medium whitespace-nowrap">{pos.slug || pos.matchName}</span>
                          </td>
                          <td className="px-3 py-2.5">
                            <span className="text-gray-600 whitespace-nowrap">{pos.teamDisplayName}</span>
                          </td>
                          <td className="px-3 py-2.5 text-right font-mono text-blue-600">{pos.entryPrice.toFixed(3)}</td>
                          <td className="px-3 py-2.5 text-right font-mono text-orange-600">{pos.currentPrice.toFixed(3)}</td>
                          <td className="px-3 py-2.5 text-right text-gray-600">{pos.shares.toLocaleString()}</td>
                          <td className="px-3 py-2.5 text-right text-gray-700 font-mono">${pos.amount.toFixed(2)}</td>
                          <td className={clsx('px-4 py-2.5 text-right font-medium whitespace-nowrap', profitColor)}>
                            {profitPrefix}${Math.abs(pos.profit).toFixed(2)}
                            <span className="ml-1 text-[10px]">({profitPrefix}{Math.abs(pos.profitPercent).toFixed(1)}%)</span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {/* Footer summary */}
              <div className="px-5 py-3 border-t border-gray-100 bg-gray-50 flex items-center justify-between flex-shrink-0 text-xs">
                <span className="text-gray-500">总投入: <span className="font-bold text-gray-700 font-mono">${viewPositions.reduce((s, p) => s + p.amount, 0).toFixed(2)}</span></span>
                <span className="text-gray-500">总浮盈: {(() => {
                  const total = viewPositions.reduce((s, p) => s + p.profit, 0);
                  return <span className={clsx('font-bold font-mono', profitColorClass(total))}>{formatSignedUsd(total)}</span>;
                })()}</span>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>

        {/* ── Trade Records Dialog ──────────────────────────────────────── */}
        <Dialog.Root open={showTradeLogs} onOpenChange={v => { setShowTradeLogs(v); if (v) { setTradeSearch(''); setTradePage(1); } }}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 bg-gray-900/50 z-50 backdrop-blur-sm" />
            <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white border border-gray-200 rounded-lg shadow-2xl w-[1280px] max-w-[96vw] h-[80vh] z-50 flex flex-col">
              {/* Header */}
              <div className="flex justify-between items-center px-5 pt-5 pb-3 border-b border-gray-100 flex-shrink-0">
                <div>
                  <Dialog.Title className="text-base font-bold text-gray-900">交易记录 — {currentAccountDisplayId}</Dialog.Title>
                  <p className="text-[11px] text-gray-400 mt-0.5">仅显示已完成订单</p>
                </div>
                <Dialog.Close className="text-gray-400 hover:text-gray-600 cursor-pointer"><X size={18} /></Dialog.Close>
              </div>
              <Dialog.Description className="sr-only">查看交易账户的已完成交易记录</Dialog.Description>

              {/* Search bar */}
              <div className="px-5 py-3 border-b border-gray-100 flex-shrink-0">
                <div className="relative w-80">
                  <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
                  <input
                    type="text"
                    placeholder="搜索订单编号、slug、原因或时间"
                    value={tradeSearch}
                    onChange={e => { setTradeSearch(e.target.value); setTradePage(1); }}
                    className="w-full pl-8 pr-3 py-1.5 border border-gray-200 rounded-md text-xs text-gray-700 bg-gray-50 focus:outline-none focus:border-[#10b981] placeholder-gray-400"
                  />
                </div>
              </div>

              {/* Table + pagination */}
              {(() => {
                const q = tradeSearch.trim().toLowerCase();
	                const accountTrades = currentAccountId
	                  ? viewTrades.filter(r => r.strategy === currentAccountId.toUpperCase())
	                  : viewTrades;
	                const filtered = accountTrades.filter(r => {
	                  if (!q) return true;
	                  const dateStr = formatTradeTime(r.timestamp);
	                  return (
	                    r.orderId.toLowerCase().includes(q) ||
	                    (r.slug || '').toLowerCase().includes(q) ||
	                    (r.reason || '').toLowerCase().includes(q) ||
	                    dateStr.toLowerCase().includes(q)
	                  );
	                });
                const totalPages = Math.max(1, Math.ceil(filtered.length / TRADE_PAGE_SIZE));
                const safePage   = Math.min(tradePage, totalPages);
                const paged      = filtered.slice((safePage - 1) * TRADE_PAGE_SIZE, safePage * TRADE_PAGE_SIZE);
                const pageNums   = Array.from({ length: totalPages }, (_, i) => i + 1)
                  .filter(p => p === 1 || p === totalPages || Math.abs(p - safePage) <= 1)
                  .reduce<(number | '...')[]>((acc, p, idx, arr) => {
                    if (idx > 0 && typeof arr[idx-1] === 'number' && (p as number) - (arr[idx-1] as number) > 1) acc.push('...');
                    acc.push(p);
                    return acc;
                  }, []);
                return (
                  <>
                    <div className="flex-1 overflow-auto custom-scrollbar">
                      {paged.length > 0 ? (
                        <table className="w-full text-xs">
                          <thead className="sticky top-0 bg-gray-50 border-b border-gray-200">
                            <tr className="text-gray-400">
                              <th className="px-4 py-2.5 text-left font-medium whitespace-nowrap">订单编号</th>
	                              <th className="px-3 py-2.5 text-left font-medium">交易员</th>
                              <th className="px-3 py-2.5 text-left font-medium">比赛</th>
                              <th className="px-3 py-2.5 text-left font-medium">方向</th>
                              <th className="px-3 py-2.5 text-left font-medium">原因</th>
                              <th className="px-3 py-2.5 text-right font-medium">成本</th>
                              <th className="px-3 py-2.5 text-right font-medium">卖出价</th>
                              <th className="px-3 py-2.5 text-right font-medium">数量</th>
                              <th className="px-3 py-2.5 text-right font-medium">金额</th>
                              <th className="px-3 py-2.5 text-right font-medium">收益</th>
                              <th className="px-4 py-2.5 text-right font-medium whitespace-nowrap">成交时间</th>
                            </tr>
                          </thead>
                          <tbody>
                            {paged.map(rec => {
                              const isBuy = rec.profit == null || rec.profitRate == null;
                              const isProfit = (rec.profit ?? 0) > 0;
                              const profitColor = isBuy ? 'text-gray-400' : profitColorClass(rec.profit ?? 0);
	                              const dateStr = formatTradeTime(rec.timestamp);
                              return (
                                <tr key={rec.id} className="border-b border-gray-50 hover:bg-gray-50 transition-colors">
                                  <td className="px-4 py-2.5">
                                    <span className="font-mono text-[11px] text-gray-500 whitespace-nowrap">{rec.orderId}</span>
                                  </td>
                                  <td className="px-3 py-2.5">
                                    <span className={clsx('font-mono font-semibold text-[11px]', rec.strategy.startsWith('R') ? 'text-green-600' : 'text-blue-500')}>
                                      {rec.strategy}
                                    </span>
                                  </td>
                                  <td className="px-3 py-2.5">
                                    <span className="text-[11px] text-gray-500 font-mono whitespace-nowrap">{rec.slug || '—'}</span>
                                  </td>
                                  <td className="px-3 py-2.5">
                                    <span className="text-[11px] text-gray-700 font-medium whitespace-nowrap">{rec.side}</span>
                                  </td>
                                  <td className="px-3 py-2.5">
                                    <span className="text-[11px] text-gray-600 whitespace-nowrap">{rec.reason || '—'}</span>
                                  </td>
                                  <td className="px-3 py-2.5 text-right text-gray-500 font-mono">{rec.entryPrice == null ? '—' : rec.entryPrice.toFixed(3)}</td>
                                  <td className="px-3 py-2.5 text-right text-gray-700 font-mono">{rec.exitPrice == null ? '—' : rec.exitPrice.toFixed(3)}</td>
                                  <td className="px-3 py-2.5 text-right text-gray-600">{Math.round(rec.quantity).toLocaleString()}</td>
                                  <td className="px-3 py-2.5 text-right text-gray-600 font-mono">${rec.amount.toFixed(2)}</td>
                                  <td className={clsx('px-3 py-2.5 text-right font-medium whitespace-nowrap', profitColor)}>
                                    {isBuy ? '—' : `${isProfit ? '+' : ''}${(rec.profit ?? 0).toFixed(2)}(${isProfit ? '+' : ''}${(rec.profitRate ?? 0).toFixed(2)}%)`}
                                  </td>
                                  <td className="px-4 py-2.5 text-right text-gray-400 font-mono text-[11px] whitespace-nowrap">{dateStr}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      ) : (
                        <div className="flex items-center justify-center h-full text-gray-400 text-sm">
                          {tradeSearch ? '未找到匹配的记录' : '暂无交易记录'}
                        </div>
                      )}
                    </div>
                    {/* Pagination footer */}
                    <div className="px-5 py-3 border-t border-gray-100 bg-gray-50 flex items-center justify-between flex-shrink-0 text-xs">
                      <span className="text-gray-400">
                        共 <span className="text-gray-700 font-medium">{filtered.length}</span> 条{tradeSearch ? ' (已过滤)' : ''}，
                        第 <span className="text-gray-700 font-medium">{safePage}</span> / {totalPages} 页
                      </span>
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => setTradePage(p => Math.max(1, p - 1))}
                          disabled={safePage === 1}
                          className="p-1 rounded border border-gray-200 text-gray-500 disabled:opacity-30 disabled:cursor-not-allowed hover:enabled:bg-gray-100 cursor-pointer transition-colors"
                        >
                          <ChevronLeft size={13} />
                        </button>
                        {pageNums.map((p, i) =>
                          p === '...' ? (
                            <span key={`dots-${i}`} className="px-1 text-gray-400 text-xs">…</span>
                          ) : (
                            <button
                              key={p}
                              onClick={() => setTradePage(p as number)}
                              className={clsx(
                                'min-w-[26px] h-[26px] rounded border text-[11px] cursor-pointer transition-colors',
                                safePage === p
                                  ? 'bg-[#10b981] text-white border-[#10b981]'
                                  : 'border-gray-200 text-gray-600 hover:bg-gray-100'
                              )}
                            >{p}</button>
                          )
                        )}
                        <button
                          onClick={() => setTradePage(p => Math.min(totalPages, p + 1))}
                          disabled={safePage === totalPages}
                          className="p-1 rounded border border-gray-200 text-gray-500 disabled:opacity-30 disabled:cursor-not-allowed hover:enabled:bg-gray-100 cursor-pointer transition-colors"
                        >
                          <ChevronRight size={13} />
                        </button>
                      </div>
                    </div>
                  </>
                );
              })()}
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>

      </div>
    </Tooltip.Provider>
  );
};
