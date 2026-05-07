import React from "react";
import { TradingProvider } from "./context/TradingContext";
import { Header } from "./components/Header";
import { Sidebar } from "./components/Sidebar";
import { MatchDetail } from "./components/MatchDetail";
import { TradingPanel } from "./components/TradingPanel";
import { LoginPage } from "./components/LoginPage";
import { checkAuthSession } from "./api/client";
import { useTrading } from "./context/TradingContext";

const SOURCE_LABELS = {
  ggs: "GGS",
  gs: "Goalserve",
  none: "未绑定",
} as const;

function FooterStatus() {
  const { collectorSettings } = useTrading();
  const sourceLabel = SOURCE_LABELS[collectorSettings.external_source] ?? "未绑定";

  return (
    <footer className="bg-white border-t border-gray-200">
      <div className="grid grid-cols-[340px_1fr_340px]">
        <div className="px-4 py-3 text-xs text-gray-500 border-gray-200">
          © 2026 PolyMarket Trading System
        </div>
        <div className="px-4 py-3 text-xs text-gray-500 text-center border-gray-200">
          实时数据采集中 | 外部数据源 {sourceLabel}
        </div>
        <div className="px-4 py-3 text-xs text-gray-500 text-right">
          v1.0.0
        </div>
      </div>
    </footer>
  );
}

function App() {
  const [authChecked, setAuthChecked] = React.useState(false);
  const [authenticated, setAuthenticated] = React.useState(false);

  React.useEffect(() => {
    let stopped = false;
    void checkAuthSession()
      .then((session) => {
        if (!stopped) {
          setAuthenticated(session.authenticated);
        }
      })
      .catch(() => {
        if (!stopped) {
          setAuthenticated(false);
        }
      })
      .finally(() => {
        if (!stopped) {
          setAuthChecked(true);
        }
      });
    return () => {
      stopped = true;
    };
  }, []);

  if (!authChecked) {
    return (
      <div className="min-h-screen w-screen bg-gray-100 text-gray-500 flex items-center justify-center">
        正在检查登录状态
      </div>
    );
  }

  if (!authenticated) {
    return <LoginPage onAuthenticated={() => setAuthenticated(true)} />;
  }

  return (
    <TradingProvider>
      <div className="min-h-screen w-screen bg-gray-50 text-gray-900 flex flex-col overflow-y-auto">
        <Header />
        <div className="flex-1 grid grid-cols-[340px_1fr_340px]">
          <Sidebar />
          <MatchDetail />
          <TradingPanel />
        </div>
        <FooterStatus />
      </div>
    </TradingProvider>
  );
}

export default App;
