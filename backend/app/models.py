"""API 请求/响应模型：保持前后端字段契约集中在这里。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


ExternalSource = Literal["gs", "ggs", "none"]
TradingMode = Literal["simulation", "real"]
ManualExternalSource = Literal["gs", "ggs"]


class CollectorSettings(BaseModel):
    collection_interval_minutes: int = Field(default=5, ge=1)
    football_volume_threshold_k: int = Field(default=500, ge=0)
    external_source: ExternalSource = "ggs"


class ManualExternalBindPayload(BaseModel):
    source: ManualExternalSource
    external_match_id: str = Field(min_length=1)


class AuthLoginPayload(BaseModel):
    password: str = Field(min_length=1)


class AuthSessionResponse(BaseModel):
    authenticated: bool
    rotation_days: int
    expires_at_utc: str | None = None


class CollectorStatus(BaseModel):
    collector_running: bool = False
    collector_last_run_at: str | None = None
    collector_last_success_at: str | None = None
    collector_last_error: str | None = None
    collector_next_run_at: str | None = None
    external_stream_enabled: bool = False
    external_stream_started: bool = False
    polymarket_ws_enabled: bool = False
    goalserve_ws_enabled: bool = False
    polymarket_ws_connected: bool = False
    pm_market_ws_enabled: bool = False
    pm_market_ws_connected: bool = False
    pm_user_ws_enabled: bool = False
    pm_user_ws_connected: bool = False
    pm_sports_ws_enabled: bool = False
    pm_sports_ws_connected: bool = False
    gs_ws_enabled: bool = False
    gs_ws_connected: bool = False
    ggs_ws_enabled: bool = False
    ggs_ws_connected: bool = False
    ggs_last_connected_at: str | None = None
    ggs_last_event_at: str | None = None
    ggs_last_error: str | None = None
    polymarket_last_connected_at: str | None = None
    polymarket_last_event_at: str | None = None
    polymarket_last_error: str | None = None
    goalserve_connected: bool = False
    goalserve_transport: str | None = None
    goalserve_last_connected_at: str | None = None
    goalserve_last_event_at: str | None = None
    goalserve_last_error: str | None = None
    matches_count: int = 0
    last_tick_source: str | None = None
    latest_tick_ts_utc: str | None = None


class TradingCreatePayload(BaseModel):
    strategy_name: str
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    affect_sports: list[str] = Field(default_factory=list)
    mode: TradingMode = "simulation"
    account_alias: str | None = None


class TradingUpdatePayload(BaseModel):
    strategy_params: dict[str, Any] | None = None
    affect_sports: list[str] | None = None


class TradingSnapshot(BaseModel):
    trading_id: str
    status: Literal["stopped", "running"]
    mode: TradingMode
    strategy_name: str
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    affect_sports: list[str] = Field(default_factory=list)


class SimulationStartPayload(BaseModel):
    initial_balance: float = Field(gt=0)
    retracement: float = Field(ge=0)
