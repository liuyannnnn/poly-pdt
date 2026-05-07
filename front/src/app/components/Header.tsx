import React, { useState, useEffect } from "react";
import { Activity } from "lucide-react";
import { CollectorStatus, fetchCollectorStatus } from "../api/client";
import { formatConnectionStatusLabel } from "./header-status";

const DEFAULT_STATUS: CollectorStatus = {
  collector_running: false,
  collector_last_run_at: null,
  collector_last_success_at: null,
  collector_last_error: null,
  collector_next_run_at: null,
  external_stream_enabled: false,
  external_stream_started: false,
  polymarket_ws_enabled: false,
  goalserve_ws_enabled: false,
  polymarket_ws_connected: false,
  pm_market_ws_enabled: false,
  pm_market_ws_connected: false,
  pm_user_ws_enabled: false,
  pm_user_ws_connected: false,
  pm_sports_ws_enabled: false,
  pm_sports_ws_connected: false,
  gs_ws_enabled: false,
  gs_ws_connected: false,
  ggs_ws_enabled: false,
  ggs_ws_connected: false,
  goalserve_connected: false,
  matches_count: 0,
  last_tick_source: null,
  latest_tick_ts_utc: null,
};

export const Header = () => {
  const [status, setStatus] = useState<CollectorStatus>(DEFAULT_STATUS);

  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const nextStatus = await fetchCollectorStatus();
        if (!stopped) {
          setStatus(nextStatus);
        }
      } catch {
        if (!stopped) {
          setStatus((prev) => ({
            ...prev,
            polymarket_ws_connected: false,
            goalserve_connected: false,
            pm_market_ws_connected: false,
            pm_user_ws_connected: false,
            pm_sports_ws_connected: false,
            gs_ws_connected: false,
            ggs_ws_connected: false,
          }));
        }
      }
    };
    void load();
    const timer = setInterval(() => {
      if (!stopped) {
        void load();
      }
    }, 5000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

  const connectionItems = [
    {
      label: "PM-Market",
      connected: Boolean(status.pm_market_ws_connected),
    },
    {
      label: "PM-User",
      connected: Boolean(status.pm_user_ws_connected),
    },
    {
      label: "PM-Sports",
      connected: Boolean(status.pm_sports_ws_connected),
    },
    {
      label: "GS-WS",
      connected: Boolean(status.gs_ws_connected ?? status.goalserve_connected),
    },
    {
      label: "GGS-WS",
      connected: Boolean(status.ggs_ws_connected),
    },
  ];

  return (
    <div className="h-14 bg-white border-b border-gray-200 flex items-center px-6 shadow-sm">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 bg-gradient-to-br from-[#10b981] to-[#059669] rounded-lg flex items-center justify-center">
          <Activity className="w-5 h-5 text-white" strokeWidth={2.5} />
        </div>
        <div className="flex flex-col">
          <h1 className="text-lg font-bold text-gray-900 leading-none">预测驱动交易系统</h1>
          <p className="text-xs text-gray-500 leading-none mt-0.5">Pridiction Drive Trading Platform</p>
        </div>
      </div>
      
      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
        {connectionItems.map((item) => (
          <div
            key={item.label}
            className="flex items-center gap-1.5 rounded-md border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-600"
            title={formatConnectionStatusLabel(item.label, item.connected)}
          >
            <span
              className={`h-2 w-2 rounded-full ${
                item.connected ? "bg-[#10b981]" : "bg-red-500"
              }`}
            />
            <span className="whitespace-nowrap">{formatConnectionStatusLabel(item.label, item.connected)}</span>
          </div>
        ))}
      </div>
    </div>
  );
};
