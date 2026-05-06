"""FastAPI 应用组装：在一个进程内启动 API、Collector、Listener 和 Trader。"""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Request
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .allsportsapi import AllSportsAPIHttpClient, AllSportsAPIWsSource
from .api import router
from .auth import AuthManager
from .collector import Collector
from .connectivity import ConnectivityChecker
from .config import Settings, load_settings
from .goalserve import GOALSERVE_WS_URL_TEMPLATE, GoalserveHttpClient, GoalserveWsSource
from .listener import BroadcastHub, Listener
from .models import CollectorSettings
from .pm_accounts import load_pm_account_configs
from .polymarket import PMClobQuoteClient, PMGammaHttpClient, PMMarketWsSource, PMSportsWsSource, PMUserWsSource
from .runtime import Runtime
from .soak import DryRunSoakRunner
from .store import RedisStore
from .timeseries import TimeseriesResampler
from .trader import TraderManager


def create_app(
    *,
    settings: Settings | None = None,
    store: Any | None = None,
    collector: Collector | None = None,
    listener: Listener | None = None,
    trader: TraderManager | None = None,
    broadcaster: BroadcastHub | None = None,
    connectivity_checker: ConnectivityChecker | None = None,
    soak_runner: DryRunSoakRunner | None = None,
    auth_manager: AuthManager | None = None,
) -> FastAPI:
    app_settings = settings or load_settings()
    app_store = store or RedisStore(app_settings)
    app_broadcaster = broadcaster or BroadcastHub()
    app_trader = trader or TraderManager(
        store=app_store,
        clob_quote_client=PMClobQuoteClient(host=app_settings.pm_clob_host),
    )
    # 默认本地运行会接 PM HTTP；GS HTTP/WS 和 PM WS 都用显式开关控制。
    pm_client = (
        PMGammaHttpClient(base_url=app_settings.pm_http_url)
        if app_settings.pm_http_enabled and app_settings.pm_http_url
        else None
    )
    gs_client = (
        GoalserveHttpClient(api_key=app_settings.goalserve_api_key)
        if app_settings.goalserve_http_enabled and app_settings.goalserve_api_key
        else None
    )
    asa_client = (
        AllSportsAPIHttpClient(
            api_key=app_settings.allsports_api_key,
            base_url=app_settings.allsports_http_url,
            timezone_name=app_settings.auth_timezone,
        )
        if app_settings.allsports_http_enabled and app_settings.allsports_api_key
        else None
    )
    default_collector_settings = CollectorSettings()
    app_collector = collector or Collector(
        store=app_store,
        pm_client=pm_client,
        gs_client=gs_client,
        asa_client=asa_client,
        broadcaster=app_broadcaster,
        trader_manager=app_trader,
    )
    if collector is None:
        app_collector.set_filters(
            football_volume_threshold_k=default_collector_settings.football_volume_threshold_k,
            upcoming_days=2,
        )
        app_collector.set_external_source(default_collector_settings.external_source)
    listener_sources = []
    if app_settings.pm_sports_ws_enabled and app_settings.pm_sports_ws_url:
        listener_sources.append(PMSportsWsSource(endpoint=app_settings.pm_sports_ws_url))
    if app_settings.pm_market_ws_enabled and app_settings.pm_market_ws_url:
        listener_sources.append(PMMarketWsSource(store=app_store, endpoint=app_settings.pm_market_ws_url))
    if app_settings.pm_user_ws_enabled and app_settings.pm_user_ws_url:
        try:
            pm_accounts = load_pm_account_configs(app_settings)
        except ValueError:
            pm_accounts = []
        if any(account.has_api_credentials for account in pm_accounts):
            listener_sources.append(
                PMUserWsSource(
                    store=app_store,
                    accounts=pm_accounts,
                    endpoint=app_settings.pm_user_ws_url,
                )
            )
    if app_settings.goalserve_ws_enabled and app_settings.goalserve_api_key:
        listener_sources.append(
            GoalserveWsSource(
                api_key=app_settings.goalserve_api_key,
                sport=app_settings.goalserve_ws_sport,
                token_url=app_settings.goalserve_ws_token_url,
                ws_url_template=app_settings.gs_ws_url or GOALSERVE_WS_URL_TEMPLATE,
            )
        )
    if app_settings.allsports_ws_enabled and app_settings.allsports_api_key:
        listener_sources.append(
            AllSportsAPIWsSource(
                api_key=app_settings.allsports_api_key,
                endpoint=app_settings.allsports_ws_url,
                timezone_name=app_settings.auth_timezone,
            )
        )
    app_connectivity_checker = connectivity_checker or ConnectivityChecker(settings=app_settings)
    app_auth_manager = auth_manager or AuthManager(store=app_store, settings=app_settings)
    app_timeseries_resampler = TimeseriesResampler(store=app_store, broadcaster=app_broadcaster)
    app_listener = listener or Listener(
        store=app_store,
        broadcaster=app_broadcaster,
        trader_manager=app_trader,
        timeseries_resampler=app_timeseries_resampler,
        sources=listener_sources,
    )
    app_soak_runner = soak_runner or DryRunSoakRunner(
        store=app_store,
        collector=app_collector,
        listener=app_listener,
        trader_manager=app_trader,
    )
    runtime = Runtime([app_collector, app_listener, app_timeseries_resampler, app_trader])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = app_settings
        app.state.store = app_store
        app.state.collector = app_collector
        app.state.listener = app_listener
        app.state.trader = app_trader
        app.state.broadcaster = app_broadcaster
        app.state.connectivity_checker = app_connectivity_checker
        app.state.soak_runner = app_soak_runner
        app.state.auth_manager = app_auth_manager
        app.state.timeseries_resampler = app_timeseries_resampler
        app.state.runtime = runtime
        app.state.enforce_collector_display_filter = collector is None
        stored_collector_settings = await app_store.get_json("settings:collector")
        if isinstance(stored_collector_settings, dict):
            app.state.collector_settings = CollectorSettings(**stored_collector_settings)
        else:
            app.state.collector_settings = default_collector_settings
            await app_store.set_json("settings:collector", default_collector_settings.model_dump())
        if collector is None:
            app_collector.set_interval_minutes(app.state.collector_settings.collection_interval_minutes)
            app_collector.set_filters(
                football_volume_threshold_k=app.state.collector_settings.football_volume_threshold_k,
                upcoming_days=2,
            )
            app_collector.set_external_source(app.state.collector_settings.external_source)
        if app_settings.auth_enabled:
            await app_auth_manager.ensure_current_password_notified()
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()
            if pm_client is not None:
                await pm_client.close()
            if gs_client is not None:
                await gs_client.close()
            if asa_client is not None:
                await asa_client.close()
            await app_store.close()

    app = FastAPI(title="PDT2.1", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if not app_settings.auth_enabled or _is_auth_exempt(request):
            return await call_next(request)
        session_id = request.cookies.get(app_settings.auth_cookie_name)
        if not await app_auth_manager.validate_session(session_id):
            return JSONResponse({"detail": "not_authenticated"}, status_code=401)
        return await call_next(request)

    app.include_router(router)
    return app


app = create_app()


def _is_auth_exempt(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True
    path = request.url.path
    return (
        path == "/api/v1/health"
        or path.startswith("/api/v1/auth/")
        or path in {"/docs", "/redoc", "/openapi.json"}
    )
