type ChartMatch = {
  teamA: { shortName: string; name?: string };
  teamB: { shortName: string; name?: string };
  marketA: { bid: number; ask: number };
  marketB: { bid: number; ask: number };
  marketDraw?: { bid: number; ask: number };
  wsTime: Date;
};

type ChartSnapshot = {
  snapshot_ts_utc: string;
  phase?: string;
  home_ask?: number | null;
  away_ask?: number | null;
  draw_ask?: number | null;
};

type ChartInput = {
  match: ChartMatch;
  snapshots: ChartSnapshot[];
};

type ChartRow = [number, "home" | "away" | "draw", number];
export type ChartPhase = "ALL" | "LIVE";

function formatPercent(value: number): string {
  return `${value.toFixed(2)}%`;
}

function formatAxisTime(value: number): string {
  return new Date(value).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatTooltipTime(value: number): string {
  return new Date(value).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function pushRow(rows: ChartRow[], ts: string, outcome: "home" | "away" | "draw", ask?: number | null) {
  if (ask == null) {
    return;
  }
  rows.push([new Date(ts).getTime(), outcome, Number((ask * 100).toFixed(2))]);
}

export function filterSnapshotsForChart(
  snapshots: ChartSnapshot[],
  phase: ChartPhase
): ChartSnapshot[] {
  return snapshots.filter((snapshot) => {
    const snapshotPhase = String(snapshot.phase ?? "").trim().toUpperCase();
    if (snapshotPhase === "LIVE") {
      return phase === "LIVE";
    }
    return phase === "ALL";
  });
}

export function chartPhaseAvailability(
  snapshots: ChartSnapshot[],
  matchStatus?: string
): { hasAll: boolean; hasLive: boolean } {
  const hasAll = snapshots.some((snapshot) => {
    const snapshotPhase = String(snapshot.phase ?? "").trim().toUpperCase();
    return snapshotPhase !== "LIVE";
  });
  const hasLiveRows = snapshots.some((snapshot) => {
    const snapshotPhase = String(snapshot.phase ?? "").trim().toUpperCase();
    return snapshotPhase === "LIVE";
  });
  const normalizedStatus = String(matchStatus ?? "").trim().toLowerCase();
  const isScheduled = !["live", "finished"].includes(normalizedStatus);
  return {
    hasAll,
    hasLive: !isScheduled && hasLiveRows,
  };
}

function buildDatasetRows(match: ChartMatch, snapshots: ChartSnapshot[]): ChartRow[] {
  const rows: ChartRow[] = [];
  for (const snapshot of snapshots) {
    pushRow(rows, snapshot.snapshot_ts_utc, "home", snapshot.home_ask);
    if (match.marketDraw) {
      pushRow(rows, snapshot.snapshot_ts_utc, "draw", snapshot.draw_ask);
    }
    pushRow(rows, snapshot.snapshot_ts_utc, "away", snapshot.away_ask);
  }
  return rows;
}

function buildAxisRange(rows: ChartRow[]) {
  const values = rows.map((row) => row[2]);
  if (values.length === 0) {
    return { min: 0, max: 100 };
  }
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const min = Math.max(0, Math.floor((minValue - 3) / 5) * 5);
  const max = Math.min(100, Math.ceil((maxValue + 3) / 5) * 5);
  return { min, max: Math.max(min + 5, max) };
}

function buildTimeRange(rows: ChartRow[]) {
  if (rows.length === 0) {
    return {};
  }
  const values = rows.map((row) => row[0]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 5 * 60 * 1000);
  return {
    min,
    max: max + span * 0.28,
  };
}

function buildSeriesConfig(
  datasetId: string,
  name: string,
  color: string,
  hasData: boolean
) {
  return {
    name,
    type: "line",
    datasetId,
    encode: {
      x: "ts",
      y: "probability",
      tooltip: ["probability"],
    },
    step: "end",
    smooth: false,
    clip: false,
    showSymbol: false,
    showAllSymbol: false,
    symbol: "circle",
    symbolSize: 8,
    endLabel: {
      show: hasData,
      distance: 16,
      formatter: ({ value }: { value: ChartRow }) =>
        `{name|${name}}\n{value|${Math.round(Number(value[2]))}%}`,
      rich: {
        name: {
          color,
          fontSize: 13,
          fontWeight: 500,
          lineHeight: 18,
        },
        value: {
          color,
          fontSize: 26,
          fontWeight: 700,
          lineHeight: 30,
        },
      },
    },
    labelLayout: { moveOverlap: "shiftY" },
    lineStyle: {
      color,
      width: 3,
      cap: "round",
      join: "round",
    },
    itemStyle: {
      color,
      borderColor: "#ffffff",
      borderWidth: 2,
    },
    emphasis: { scale: false },
  };
}

export function buildMatchPriceChartOption({ match, snapshots }: ChartInput) {
  const rows = buildDatasetRows(match, snapshots);
  const axisRange = buildAxisRange(rows);
  const timeRange = buildTimeRange(rows);

  const datasets = [
    {
      id: "raw",
      dimensions: ["ts", "outcome", "probability"],
      source: rows,
    },
    {
      id: "home",
      fromDatasetId: "raw",
      transform: {
        type: "filter",
        config: { dimension: "outcome", "=": "home" },
      },
    },
    ...(match.marketDraw
      ? [
          {
            id: "draw",
            fromDatasetId: "raw",
            transform: {
              type: "filter",
              config: { dimension: "outcome", "=": "draw" },
            },
          },
        ]
      : []),
    {
      id: "away",
      fromDatasetId: "raw",
      transform: {
        type: "filter",
        config: { dimension: "outcome", "=": "away" },
      },
    },
  ];

  return {
    animation: false,
    backgroundColor: "transparent",
    dataset: datasets,
    grid: { top: 12, right: 64, bottom: 30, left: 12, containLabel: false },
    xAxis: {
      type: "time",
      ...timeRange,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        show: true,
        color: "#94a3b8",
        fontSize: 12,
        margin: 16,
        formatter: (value: number) => formatAxisTime(value),
      },
      splitLine: { show: false },
      boundaryGap: false,
    },
    yAxis: {
      type: "value",
      min: axisRange.min,
      max: axisRange.max,
      position: "right",
      splitNumber: 4,
      axisLabel: {
        formatter: "{value}%",
        color: "#94a3b8",
        fontSize: 12,
        margin: 10,
      },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: "#cbd5e1", type: "dotted", opacity: 1 } },
    },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#ffffff",
      borderColor: "#e2e8f0",
      borderWidth: 1,
      textStyle: { color: "#334155", fontSize: 12 },
      formatter: (params: Array<{ axisValue?: number; marker: string; seriesName: string; value: ChartRow }>) => {
        const ts = Number(params[0]?.axisValue ?? params[0]?.value?.[0] ?? 0);
        const rows = params.map((item) => `${item.marker}${item.seriesName}: <b>${formatPercent(Number(item.value[2]))}</b>`);
        return [formatTooltipTime(ts), ...rows].join("<br/>");
      },
    },
    legend: { show: false },
    series: [
      buildSeriesConfig("home", match.teamA.name ?? match.teamA.shortName, "#0b6ea8", rows.some((row) => row[1] === "home")),
      ...(match.marketDraw
        ? [buildSeriesConfig("draw", "Draw", "#94a3b8", rows.some((row) => row[1] === "draw"))]
        : []),
      buildSeriesConfig("away", match.teamB.name ?? match.teamB.shortName, "#8ad7de", rows.some((row) => row[1] === "away")),
    ],
  };
}
