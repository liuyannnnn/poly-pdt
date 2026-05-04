import { describe, expect, it } from "vitest";
import { buildMatchPriceChartOption, chartPhaseAvailability, filterSnapshotsForChart } from "./match-chart";


describe("buildMatchPriceChartOption", () => {
  it("renders a time-based chart with visible time labels and tooltip time", () => {
    const option = buildMatchPriceChartOption({
      match: {
        teamA: { shortName: "Lakers" },
        teamB: { shortName: "Rockets" },
        marketA: { bid: 0.44, ask: 0.45 },
        marketB: { bid: 0.55, ask: 0.56 },
        homePrice: 0.445,
        awayPrice: 0.555,
        marketDraw: undefined,
        wsTime: new Date("2026-03-16T13:00:00Z"),
      },
      snapshots: [
        {
          snapshot_ts_utc: "2026-03-16T12:55:00Z",
          home_ask: 0.435,
          away_ask: 0.565,
        },
        {
          snapshot_ts_utc: "2026-03-16T12:58:00Z",
          home_ask: 0.45,
          away_ask: 0.55,
        },
      ],
    });

    expect(option.xAxis.type).toBe("time");
    expect(option.xAxis.axisLabel.show).toBe(true);
    expect(option.series[0].endLabel.show).toBe(true);
    expect(option.dataset).toHaveLength(3);
    expect(option.dataset[0].id).toBe("raw");
    expect(option.dataset[0].source).toHaveLength(4);
    expect(option.series[0].datasetId).toBe("home");
    expect(option.series[0].encode.x).toBe("ts");
    expect(option.series[0].encode.y).toBe("probability");
    expect(option.series[0].showSymbol).toBe(false);
    expect(option.series[1].showSymbol).toBe(false);

    const tooltipText = option.tooltip.formatter([
      {
        axisValue: new Date("2026-03-16T13:00:00Z").getTime(),
        marker: "•",
        seriesName: "Lakers",
        value: [new Date("2026-03-16T13:00:00Z").getTime(), "home", 44],
      },
      {
        axisValue: new Date("2026-03-16T13:00:00Z").getTime(),
        marker: "•",
        seriesName: "Rockets",
        value: [new Date("2026-03-16T13:00:00Z").getTime(), "away", 55],
      },
    ]);

    expect(tooltipText).toContain("2026");
    expect(tooltipText).toContain("Lakers");
    expect(tooltipText).toContain("44.00%");
  });

  it("prefers ask1 snapshots and current ask1 values for the chart series", () => {
    const option = buildMatchPriceChartOption({
      match: {
        teamA: { shortName: "PSG" },
        teamB: { shortName: "LIV" },
        marketA: { bid: 0.52, ask: 0.57 },
        marketB: { bid: 0.21, ask: 0.22 },
        marketDraw: { bid: 0.22, ask: 0.23 },
        homePrice: 0.54,
        awayPrice: 0.2,
        drawPrice: 0.21,
        wsTime: new Date("2026-04-06T10:00:00Z"),
      },
      snapshots: [
        {
          snapshot_ts_utc: "2026-04-06T09:55:00Z",
          home_bid: 0.5,
          home_ask: 0.55,
          home_price: 0.52,
          away_bid: 0.2,
          away_ask: 0.21,
          away_price: 0.19,
          draw_bid: 0.22,
          draw_ask: 0.24,
          draw_price: 0.21,
        },
      ],
    });

    expect(option.dataset[0].source[0][2]).toBe(55);
    expect(option.dataset[0].source[1][2]).toBe(24);
    expect(option.dataset[0].source[2][2]).toBe(21);
    expect(option.series[0].datasetId).toBe("home");
    expect(option.series[1].datasetId).toBe("draw");
    expect(option.series[2].datasetId).toBe("away");
  });

  it("uses the exact snapshot timestamp from Redis without rounding", () => {
    const timestamp = "2026-05-02T11:28:12.323Z";
    const option = buildMatchPriceChartOption({
      match: {
        teamA: { shortName: "BRE" },
        teamB: { shortName: "WHU" },
        marketA: { bid: 0.51, ask: 0.52 },
        marketB: { bid: 0.24, ask: 0.25 },
        marketDraw: { bid: 0.23, ask: 0.24 },
        wsTime: new Date(timestamp),
      },
      snapshots: [
        {
          snapshot_ts_utc: timestamp,
          home_ask: 0.52,
          draw_ask: 0.24,
          away_ask: 0.25,
        },
      ],
    });

    expect(option.dataset[0].source[0][0]).toBe(new Date(timestamp).getTime());
  });

  it("renders a light step chart with right-side percentage axis", () => {
    const option = buildMatchPriceChartOption({
      match: {
        teamA: { shortName: "PSG" },
        teamB: { shortName: "LIV" },
        marketA: { bid: 0.52, ask: 0.57 },
        marketB: { bid: 0.21, ask: 0.22 },
        marketDraw: { bid: 0.22, ask: 0.23 },
        homePrice: 0.54,
        awayPrice: 0.2,
        drawPrice: 0.21,
        wsTime: new Date("2026-04-06T10:00:00Z"),
      },
      snapshots: [],
    });

    expect(option.backgroundColor).toBe("transparent");
    expect(option.grid.right).toBeLessThanOrEqual(72);
    expect(option.yAxis.position).toBe("right");
    expect(option.series[0].step).toBe("end");
    expect(option.series[0].showSymbol).toBe(false);
    expect(option.series[0].endLabel.show).toBe(false);
    expect(option.dataset[0].source).toHaveLength(0);
  });

  it("extends only the chart domain so end labels sit inside the plot instead of on the right edge", () => {
    const latest = new Date("2026-05-02T13:10:00Z").getTime();
    const option = buildMatchPriceChartOption({
      match: {
        teamA: { shortName: "VIL" },
        teamB: { shortName: "LEV" },
        marketA: { bid: 0.75, ask: 0.76 },
        marketB: { bid: 0.06, ask: 0.07 },
        marketDraw: { bid: 0.18, ask: 0.19 },
        wsTime: new Date(latest),
      },
      snapshots: [
        { snapshot_ts_utc: "2026-05-02T13:00:00Z", home_ask: 0.75, draw_ask: 0.19, away_ask: 0.07 },
        { snapshot_ts_utc: "2026-05-02T13:10:00Z", home_ask: 0.76, draw_ask: 0.19, away_ask: 0.07 },
      ],
    });

    expect(option.xAxis.max).toBeGreaterThan(latest);
    expect(option.series[0].endLabel.distance).toBe(16);
  });

  it("filters snapshots by ALL/LIVE phase", () => {
    const snapshots = [
      { snapshot_ts_utc: "2026-04-06T09:55:00Z", phase: "all", home_ask: 0.55 },
      { snapshot_ts_utc: "2026-04-06T10:05:00Z", phase: "live", home_ask: 0.65 },
      { snapshot_ts_utc: "2026-04-06T10:06:00Z", phase: "LIVE", home_ask: 0.66 },
    ];

    expect(filterSnapshotsForChart(snapshots, "ALL")).toHaveLength(1);
    expect(filterSnapshotsForChart(snapshots, "LIVE")).toHaveLength(2);
  });

  it("does not mix ALL snapshots into LIVE when no live phase rows exist", () => {
    const snapshots = [
      { snapshot_ts_utc: "2026-04-06T09:55:00Z", phase: "all", home_ask: 0.55 },
      { snapshot_ts_utc: "2026-04-06T10:05:00Z", phase: "ALL", home_ask: 0.65 },
    ];

    expect(filterSnapshotsForChart(snapshots, "LIVE")).toHaveLength(0);
  });

  it("keeps LIVE unavailable for scheduled matches even if stale live rows exist", () => {
    const snapshots = [
      { snapshot_ts_utc: "2026-04-06T09:55:00Z", phase: "ALL", home_ask: 0.55 },
      { snapshot_ts_utc: "2026-04-06T10:05:00Z", phase: "LIVE", home_ask: 0.65 },
    ];

    expect(chartPhaseAvailability(snapshots, "Scheduled")).toEqual({
      hasAll: true,
      hasLive: false,
    });
    expect(chartPhaseAvailability(snapshots, "Finished").hasLive).toBe(true);
  });
});
