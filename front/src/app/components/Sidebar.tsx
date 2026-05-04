import React, { useEffect, useMemo, useState } from 'react';
import { Match, useTrading } from '../context/TradingContext';
import { Search, Filter, Settings, History, X } from 'lucide-react';
import { clsx } from 'clsx';
import { formatCompactUsdVolume, groupMatchesByStatus } from '../api/trading-mappers';
import * as Dialog from '@radix-ui/react-dialog';
import {
  bindExternalMatch,
  ExternalMatchCandidate,
  fetchExternalMatchCandidates,
} from '../api/client';

export const Sidebar = () => {
  const {
    matches,
    historyMatches,
    historyPage,
    historyHasMore,
    loadHistoryPage,
    selectMatch,
    selectedMatchId,
    collectorSettings,
    updateCollectorSettings,
    refreshMatches,
  } = useTrading();
  const [searchTerm, setSearchTerm] = useState('');
  const [showFilter, setShowFilter] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [selectedSport, setSelectedSport] = useState<string>('All');
  const [showHistory, setShowHistory] = useState(false);
  const [manualMatch, setManualMatch] = useState<Match | null>(null);
  const [manualCandidates, setManualCandidates] = useState<ExternalMatchCandidate[]>([]);
  const [manualLoading, setManualLoading] = useState(false);
  const [manualError, setManualError] = useState<string | null>(null);
  
  // Settings State with volume thresholds and collection interval
  const [settings, setSettings] = useState({
    footballVol: 50,
    collectionInterval: 15,
    externalSource: 'asa' as 'asa' | 'gs' | 'none'
  });

  useEffect(() => {
    setSettings((prev) => ({
      ...prev,
      collectionInterval: collectorSettings.collection_interval_minutes,
      footballVol: collectorSettings.football_volume_threshold_k,
      externalSource: collectorSettings.external_source,
    }));
  }, [collectorSettings]);

  const sourceList = showHistory ? historyMatches : matches.filter((match) => match.status !== 'Finished');
  const hasActiveFilters = searchTerm.trim().length > 0 || selectedSport !== 'All';

  const filteredMatches = sourceList.filter(match => {
    const isSupportedSport = match.sport === 'Football';
    if (!isSupportedSport) {
      return false;
    }
    const matchesSearch = match.teamA.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                          match.teamB.name.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesSport = selectedSport === 'All' || match.sport === selectedSport;
    return matchesSearch && matchesSport;
  });

  const groupedMatches = useMemo(() => groupMatchesByStatus(filteredMatches), [filteredMatches]);

  const openManualMatchDialog = async (match: Match) => {
    setManualMatch(match);
    setManualCandidates([]);
    setManualError(null);
    setManualLoading(true);
    try {
      const source = collectorSettings.external_source === 'none' ? 'asa' : collectorSettings.external_source;
      const rows = await fetchExternalMatchCandidates(match.id, source, 50);
      setManualCandidates(rows);
    } catch (error) {
      setManualError(String(error));
    } finally {
      setManualLoading(false);
    }
  };

  const confirmManualBind = async (candidate: ExternalMatchCandidate) => {
    if (!manualMatch) {
      return;
    }
    setManualError(null);
    setManualLoading(true);
    try {
      await bindExternalMatch(manualMatch.id, candidate.source, candidate.external_match_id);
      await refreshMatches();
      setManualMatch(null);
    } catch (error) {
      setManualError(String(error));
    } finally {
      setManualLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-gray-50 text-sm relative">
      
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-r border-gray-200 bg-white">
        <div className="flex items-center gap-2">
          <h2 className="text-gray-900 font-bold text-base">比赛</h2>
          {showHistory && (
            <span className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded font-medium">历史</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button 
            onClick={() => {
              const next = !showHistory;
              setShowHistory(next);
              if (next) {
                void loadHistoryPage(0);
              }
              setShowFilter(false);
              setShowSettings(false);
            }}
            title="历史比赛"
            className={clsx(
              "p-1.5 rounded-md transition-colors cursor-pointer",
              showHistory ? "bg-[#10b981]/20 text-[#10b981]" : "hover:bg-gray-100 text-gray-500"
            )}
          >
            <History size={16} />
          </button>
          <button 
            onClick={() => {
              setShowFilter(!showFilter);
              setShowSettings(false);
            }}
            className={clsx(
              "p-1.5 rounded-md transition-colors cursor-pointer",
              showFilter ? "bg-[#10b981]/20 text-[#10b981]" : "hover:bg-gray-100 text-gray-500"
            )}
          >
            <Filter size={16} />
          </button>
          <button 
            onClick={() => {
              setShowSettings(!showSettings);
              setShowFilter(false);
            }}
            className={clsx(
              "p-1.5 rounded-md transition-colors cursor-pointer",
              showSettings ? "bg-[#10b981]/20 text-[#10b981]" : "hover:bg-gray-100 text-gray-500"
            )}
          >
            <Settings size={16} />
          </button>
        </div>
      </div>

      {/* Filter Popup */}
      {showFilter && (
        <div className="absolute top-14 left-3 right-3 bg-white border border-gray-200 rounded-md shadow-xl p-3 z-50">
          <div className="space-y-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" size={14} />
              <input 
                type="text"
                placeholder="搜索比赛..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full bg-gray-50 border border-gray-200 rounded-md pl-8 pr-3 py-1.5 text-xs text-gray-900 placeholder-gray-400 focus:outline-none focus:border-[#10b981]"
              />
            </div>
            <div>
              <label className="text-[10px] text-gray-500 block mb-2">体育项目</label>
              <div className="flex flex-wrap gap-2">
                {['All', 'Football'].map(sport => (
                  <button
                    key={sport}
                    onClick={() => setSelectedSport(sport)}
                    className={clsx(
                      "px-3 py-1 rounded-md text-xs font-medium cursor-pointer transition-colors",
                      selectedSport === sport 
                        ? "bg-[#10b981] text-white" 
                        : "bg-gray-50 text-gray-600 border border-gray-200 hover:bg-gray-100"
                    )}
                  >
                    {sport === 'All' ? '全部' : '足球'}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Settings Popup */}
      {showSettings && (
        <div className="absolute top-14 left-3 right-3 bg-white border border-gray-200 rounded-md shadow-xl p-4 z-50 max-w-md">
          <div className="text-sm font-bold text-gray-900 mb-4">数据采集设置</div>
          
          <div className="space-y-4 max-h-96 overflow-y-auto custom-scrollbar pr-2">
            {/* Collection Interval */}
            <div className="pb-3 border-b border-gray-200">
              <div className="flex items-center justify-between">
                <label className="text-sm text-gray-800 font-medium">采集间隔</label>
                <div className="flex items-center gap-1">
                  <input 
                    type="number"
                    min="1"
                    value={settings.collectionInterval}
                    onChange={(e) => setSettings({...settings, collectionInterval: parseInt(e.target.value) || 1})}
                    className="w-16 bg-gray-50 border border-gray-200 rounded px-2 py-0.5 text-xs text-gray-900 focus:outline-none focus:border-[#10b981]"
                  />
                  <span className="text-xs text-gray-500">分钟</span>
                </div>
              </div>
            </div>

            <div className="pb-3 border-b border-gray-200">
              <div className="flex items-center justify-between gap-3">
                <label className="text-sm text-gray-800 font-medium">外部数据源</label>
                <select
                  value={settings.externalSource}
                  onChange={(e) => setSettings({...settings, externalSource: e.target.value as 'asa' | 'gs' | 'none'})}
                  className="bg-gray-50 border border-gray-200 rounded px-2 py-1 text-xs text-gray-900 focus:outline-none focus:border-[#10b981]"
                >
                  <option value="asa">AllSportsAPI</option>
                  <option value="gs">Goalserve</option>
                  <option value="none">不使用</option>
                </select>
              </div>
              <div className="mt-1 text-[10px] text-gray-400">PM 始终作为基础数据源</div>
            </div>

            {/* Football */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm text-gray-800 font-medium">足球</span>
                <div className="flex items-center gap-1">
                  <span className="text-xs text-gray-500">Vol $</span>
                  <input 
                    type="number" min="0" value={settings.footballVol}
                    onChange={(e) => setSettings({...settings, footballVol: parseInt(e.target.value) || 0})}
                    className="w-18 bg-gray-50 border border-gray-200 rounded px-2 py-0.5 text-xs text-gray-900 focus:outline-none focus:border-[#10b981]"
                  />
                  <span className="text-xs text-gray-500">K</span>
                </div>
              </div>
            </div>
          </div>
          
          <button
            onClick={() => {
              void updateCollectorSettings({
                collection_interval_minutes: settings.collectionInterval,
                football_volume_threshold_k: settings.footballVol,
                external_source: settings.externalSource,
              });
              setShowSettings(false);
            }}
            className="w-full mt-4 bg-[#10b981] hover:bg-[#0ea571] text-white text-xs py-2 rounded-md transition-colors cursor-pointer"
          >
            保存设置
          </button>
        </div>
      )}

      {/* Match List */}
      <div className="flex-1 overflow-y-auto custom-scrollbar p-2 space-y-2 bg-gray-50">
        {filteredMatches.length === 0 && (
          <div className="text-center text-gray-400 text-xs pt-8">
            {showHistory
              ? "暂无历史比赛数据"
              : sourceList.length === 0
                ? "暂无比赛数据"
                : hasActiveFilters
                  ? "当前筛选条件下暂无比赛"
                  : "暂无比赛数据"}
          </div>
        )}
        {groupedMatches.live.length > 0 && (
          <>
            <div className="px-1 pt-1 pb-0.5 text-[10px] font-bold text-red-500 uppercase">Live</div>
            {groupedMatches.live.map(match => (
              <MatchItem
                key={match.id}
                match={match}
                isSelected={selectedMatchId === match.id}
                onClick={() => selectMatch(match.id)}
                onManualMatch={openManualMatchDialog}
              />
            ))}
          </>
        )}
        {groupedMatches.pre.length > 0 && (
          <>
            <div className="px-1 pt-2 pb-0.5 text-[10px] font-bold text-gray-500 uppercase">Pre</div>
            {groupedMatches.pre.map(match => (
              <MatchItem
                key={match.id}
                match={match}
                isSelected={selectedMatchId === match.id}
                onClick={() => selectMatch(match.id)}
                onManualMatch={openManualMatchDialog}
              />
            ))}
          </>
        )}
        {showHistory && groupedMatches.finished.length > 0 && (
          <>
            <div className="px-1 pt-2 pb-0.5 text-[10px] font-bold text-gray-400 uppercase">Finished</div>
            {groupedMatches.finished.map(match => (
              <MatchItem
                key={match.id}
                match={match}
                isSelected={selectedMatchId === match.id}
                onClick={() => selectMatch(match.id)}
                onManualMatch={openManualMatchDialog}
              />
            ))}
          </>
        )}
        {showHistory && (
          <div className="flex items-center justify-between gap-2 px-1 pt-3">
            <button
              onClick={() => {
                if (historyPage === 0) {
                  return;
                }
                void loadHistoryPage(historyPage - 1);
              }}
              disabled={historyPage === 0}
              className={clsx(
                "px-2 py-1 rounded-md text-[11px] border transition-colors",
                historyPage === 0
                  ? "border-gray-200 text-gray-300 cursor-not-allowed bg-gray-50"
                  : "border-gray-200 text-gray-600 bg-white hover:bg-gray-100 cursor-pointer"
              )}
            >
              上一页
            </button>
            <div className="text-[11px] text-gray-500">第 {historyPage + 1} 页</div>
            <button
              onClick={() => {
                if (!historyHasMore) {
                  return;
                }
                void loadHistoryPage(historyPage + 1);
              }}
              disabled={!historyHasMore}
              className={clsx(
                "px-2 py-1 rounded-md text-[11px] border transition-colors",
                !historyHasMore
                  ? "border-gray-200 text-gray-300 cursor-not-allowed bg-gray-50"
                  : "border-gray-200 text-gray-600 bg-white hover:bg-gray-100 cursor-pointer"
              )}
            >
              下一页
            </button>
          </div>
        )}
      </div>
      <Dialog.Root open={manualMatch !== null} onOpenChange={(open) => !open && setManualMatch(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-gray-900/45 z-50" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 bg-white border border-gray-200 rounded-lg shadow-2xl w-[720px] max-w-[92vw] max-h-[80vh] flex flex-col">
            <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-gray-200">
              <div>
                <Dialog.Title className="text-base font-bold text-gray-900">手工匹配外部数据源</Dialog.Title>
                <Dialog.Description className="text-xs text-gray-500 mt-1">
                  {manualMatch ? `${manualMatch.teamA.name} / ${manualMatch.teamB.name}` : ''}
                </Dialog.Description>
              </div>
              <Dialog.Close className="p-1 text-gray-400 hover:text-gray-700 cursor-pointer">
                <X size={18} />
              </Dialog.Close>
            </div>
            <div className="p-4 overflow-y-auto custom-scrollbar">
              {manualLoading && <div className="text-sm text-gray-500 py-8 text-center">正在拉取候选比赛...</div>}
              {manualError && <div className="text-xs text-red-500 mb-3">{manualError}</div>}
              {!manualLoading && manualCandidates.length === 0 && (
                <div className="text-sm text-gray-500 py-8 text-center">暂无候选比赛</div>
              )}
              <div className="space-y-2">
                {manualCandidates.map((candidate) => (
                  <div key={`${candidate.source}-${candidate.external_match_id}`} className="border border-gray-200 rounded-md p-3 bg-white">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
                          <span className="font-mono uppercase">{candidate.source}</span>
                          <span>{formatCandidateTime(candidate.start_time_utc)}</span>
                          <span>{candidate.status || '-'}</span>
                        </div>
                        <div className="text-sm font-semibold text-gray-900 truncate">
                          {candidate.home_team || '-'} / {candidate.away_team || '-'}
                        </div>
                        <div className="text-xs text-gray-500 mt-1 truncate">
                          {candidate.league || '未提供联赛'} · ID {candidate.external_match_id}
                        </div>
                      </div>
                      <div className="text-right shrink-0">
                        <div className="text-sm font-bold text-gray-900">{(candidate.confidence * 100).toFixed(1)}%</div>
                        <div className="text-[10px] text-gray-400">相似度</div>
                      </div>
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-3">
                      <div className="text-xs text-gray-500">
                        比分 {candidate.score_home ?? '-'}-{candidate.score_away ?? '-'}
                        {candidate.match_time ? ` · ${candidate.match_time}` : ''}
                      </div>
                      <button
                        onClick={() => void confirmManualBind(candidate)}
                        disabled={manualLoading}
                        className="px-3 py-1.5 rounded-md bg-[#10b981] text-white text-xs font-semibold hover:bg-[#0ea571] disabled:opacity-50 cursor-pointer"
                      >
                        绑定
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
};

const MatchItem = ({
  match,
  isSelected,
  onClick,
  onManualMatch,
}: {
  match: Match;
  isSelected: boolean;
  onClick: () => void;
  onManualMatch: (match: Match) => void;
}) => {
  const isLive = match.status === 'Live';
  const isFinished = match.status === 'Finished';
  
  const formatWsTime = (date: Date) => {
    const h = date.getHours().toString().padStart(2, '0');
    const m = date.getMinutes().toString().padStart(2, '0');
    const s = date.getSeconds().toString().padStart(2, '0');
    return `${h}:${m}:${s}`;
  };

  // Always show match start time next to sport icon
  const formatStartTime = (date: Date) => {
    const mo = (date.getMonth() + 1).toString().padStart(2, '0');
    const d = date.getDate().toString().padStart(2, '0');
    const h = date.getHours().toString().padStart(2, '0');
    const m = date.getMinutes().toString().padStart(2, '0');
    return `${mo}-${d} ${h}:${m}`;
  };

  const getSportIcon = () => {
    if (match.sport === 'Football') return <div className="w-3.5 h-3.5 flex items-center justify-center text-[11px]">⚽</div>;
    return <div className="w-3.5 h-3.5 flex items-center justify-center text-[11px]">⚽</div>;
  };
  
  return (
    <div 
      onClick={onClick}
      className={clsx(
        "rounded-md p-2 border cursor-pointer transition-all relative bg-white text-gray-800",
        isSelected ? "border-[#10b981] shadow-md" : "border-gray-200 hover:border-gray-300"
      )}
    >
      {/* Top Row: Status and Start Time */}
      <div className="flex justify-between items-center mb-1.5">
        <div className="flex items-center gap-1.5">
          {isLive ? (
            <>
              <div className="w-1.5 h-1.5 rounded-full bg-red-500" />
              <span className="text-[11px] font-bold text-red-500 tracking-wide leading-none">Live</span>
            </>
          ) : isFinished ? (
            <span className="text-[11px] font-bold text-gray-400 tracking-wide leading-none">End</span>
          ) : (
            <span className="text-[11px] font-bold text-gray-500 tracking-wide leading-none">PRE</span>
          )}
          {!match.externalBound && match.bindingStatus !== 'matched' && (
            <button
              onClick={(event) => {
                event.stopPropagation();
                onManualMatch(match);
              }}
              className="text-[10px] font-semibold text-amber-600 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded cursor-pointer hover:bg-amber-100"
            >
              Unmatched
            </button>
          )}
        </div>
        <div className="flex items-center gap-1.5 text-[#10b981] font-medium text-[11px] [&_svg]:stroke-none [&_svg]:fill-current">
          {getSportIcon()}
          <span>{formatStartTime(match.startTime)}</span>
        </div>
      </div>

      {/* Middle Row: Teams and Score */}
      <div className="flex justify-between items-center mb-1.5 px-1">
        <div className="flex-1 text-sm font-bold text-gray-900 truncate leading-tight">
          {match.teamA.shortName}
        </div>
        <div className="px-3 text-sm font-bold flex items-center gap-1.5">
          <span className="text-gray-700">{match.scoreA}</span>
          <span className="text-gray-400">-</span>
          <span className="text-gray-700">{match.scoreB}</span>
        </div>
        <div className="flex-1 text-sm font-bold text-gray-900 truncate text-right leading-tight">
          {match.teamB.shortName}
        </div>
      </div>

      {/* Bottom Row: Probabilities */}
      <div className="flex justify-between items-center gap-1.5 mb-1.5">
        <div className="flex-1 text-center bg-gray-50 border border-gray-200 rounded-md py-0.5">
          <span className="text-blue-500 font-medium text-xs leading-none">{(match.marketA.bid * 100).toFixed(0)}%</span>
        </div>
        {match.marketDraw && (
          <div className="flex-1 text-center bg-gray-50 border border-gray-200 rounded-md py-0.5">
            <span className="text-gray-600 font-medium text-xs leading-none">{(match.marketDraw.bid * 100).toFixed(0)}%</span>
          </div>
        )}
        <div className="flex-1 text-center bg-gray-50 border border-gray-200 rounded-md py-0.5">
          <span className="text-orange-500 font-medium text-xs leading-none">{(match.marketB.bid * 100).toFixed(0)}%</span>
        </div>
      </div>

      {/* Footer: Volume and Time */}
      <div className="flex justify-between items-center text-[10px] text-gray-400 leading-none">
        <span><span className="text-gray-300">Vol:</span>{formatCompactUsdVolume(match.totalVolume)}</span>
        <span className="font-mono">{formatWsTime(match.wsTime)}</span>
      </div>
    </div>
  );
};

function formatCandidateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '-';
  }
  const mo = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  const h = String(date.getHours()).padStart(2, '0');
  const m = String(date.getMinutes()).padStart(2, '0');
  return `${mo}-${d} ${h}:${m}`;
}
