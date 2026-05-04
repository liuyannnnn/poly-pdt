"""运行配置入口：默认本地 Redis 可用，外部 WS 用显式环境变量开启。"""

from dataclasses import dataclass
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

from .allsportsapi import ALLSPORTS_HTTP_URL, ALLSPORTS_WS_URL
from .goalserve import resolve_goalserve_api_key
from .polymarket import PM_GAMMA_EVENTS_URL, PM_MARKET_WS_URL, PM_SPORTS_WS_URL, PM_USER_WS_URL


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    app_name: str = "PDT2.1"
    api_prefix: str = "/api/v1"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_socket_timeout_seconds: float = 2.0
    heartbeat_interval_seconds: float = 15.0
    connectivity_timeout_seconds: float = 5.0
    pm_http_url: str | None = PM_GAMMA_EVENTS_URL
    pm_http_enabled: bool = True
    pm_sports_ws_url: str | None = PM_SPORTS_WS_URL
    pm_sports_ws_enabled: bool = False
    pm_market_ws_url: str | None = PM_MARKET_WS_URL
    pm_market_ws_enabled: bool = False
    pm_user_ws_url: str | None = PM_USER_WS_URL
    pm_user_ws_enabled: bool = False
    pm_clob_host: str = "https://clob.polymarket.com"
    pm_data_api_url: str = "https://data-api.polymarket.com"
    pm_accounts_json: str | None = None
    gs_http_url: str | None = None
    gs_ws_url: str | None = None
    goalserve_feeds_file: str | None = str(PROJECT_ROOT / "Goalserve-API" / "fullsoccer.txt")
    goalserve_api_key: str | None = None
    goalserve_http_enabled: bool = False
    goalserve_ws_enabled: bool = False
    goalserve_ws_token_url: str = "http://live.goalserve.com/api/v1/auth/gettoken"
    goalserve_ws_sport: str = "soccer"
    goalserve_widget_url_template: str | None = None
    allsports_api_key: str | None = None
    allsports_http_url: str = ALLSPORTS_HTTP_URL
    allsports_http_enabled: bool = False
    allsports_ws_url: str = ALLSPORTS_WS_URL
    allsports_ws_enabled: bool = False
    allsports_widget_url_template: str | None = None
    auth_enabled: bool = False
    auth_rotation_days: int = 3
    auth_session_ttl_seconds: int | None = None
    auth_password_length: int = 8
    auth_timezone: str = "Asia/Shanghai"
    auth_cookie_name: str = "pdt_session"
    auth_email_to: str | None = None
    auth_email_subject: str | None = None
    auth_notify_channel: str = "console"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_security: str = "starttls"
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )


def load_settings() -> Settings:
    if "pytest" not in sys.modules:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    # Goalserve key 可从 env 或本地 feeds 文档解析，但不会写入 Redis 或 API 响应。
    origins = os.getenv("PDT_CORS_ORIGINS")
    parsed_origins = (
        tuple(item.strip() for item in origins.split(",") if item.strip())
        if origins
        else Settings.cors_origins
    )
    goalserve_feeds_file = os.getenv("GOALSERVE_FEEDS_FILE", Settings.goalserve_feeds_file or "")
    explicit_goalserve_key = os.getenv("GOALSERVE_API_KEY")
    default_ws_enabled = "0" if "pytest" in sys.modules else "1"
    return Settings(
        redis_url=os.getenv("REDIS_URL", Settings.redis_url),
        redis_socket_timeout_seconds=float(
            os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", Settings.redis_socket_timeout_seconds)
        ),
        heartbeat_interval_seconds=float(
            os.getenv("PDT_HEARTBEAT_INTERVAL_SECONDS", Settings.heartbeat_interval_seconds)
        ),
        connectivity_timeout_seconds=float(
            os.getenv("PDT_CONNECTIVITY_TIMEOUT_SECONDS", Settings.connectivity_timeout_seconds)
        ),
        pm_http_url=os.getenv("PM_HTTP_URL", Settings.pm_http_url),
        pm_http_enabled=os.getenv("PM_HTTP_ENABLED", "1").lower() in {"1", "true", "yes"},
        pm_sports_ws_url=os.getenv("PM_SPORTS_WS_URL", Settings.pm_sports_ws_url),
        pm_sports_ws_enabled=os.getenv("PM_SPORTS_WS_ENABLED", default_ws_enabled).lower()
        in {"1", "true", "yes"},
        pm_market_ws_url=os.getenv("PM_MARKET_WS_URL", Settings.pm_market_ws_url),
        pm_market_ws_enabled=os.getenv("PM_MARKET_WS_ENABLED", default_ws_enabled).lower()
        in {"1", "true", "yes"},
        pm_user_ws_url=os.getenv("PM_USER_WS_URL", Settings.pm_user_ws_url or "") or None,
        pm_user_ws_enabled=os.getenv("PM_USER_WS_ENABLED", default_ws_enabled).lower()
        in {"1", "true", "yes"},
        pm_clob_host=os.getenv("PM_CLOB_HOST", Settings.pm_clob_host),
        pm_data_api_url=os.getenv("PM_DATA_API_URL", Settings.pm_data_api_url),
        pm_accounts_json=os.getenv("PM_ACCOUNTS_JSON", Settings.pm_accounts_json or "") or None,
        gs_http_url=os.getenv("GS_HTTP_URL", Settings.gs_http_url),
        gs_ws_url=os.getenv("GS_WS_URL", Settings.gs_ws_url),
        goalserve_feeds_file=goalserve_feeds_file,
        goalserve_api_key=explicit_goalserve_key or resolve_goalserve_api_key(goalserve_feeds_file),
        goalserve_http_enabled=os.getenv("GOALSERVE_HTTP_ENABLED", "0").lower() in {"1", "true", "yes"},
        goalserve_ws_enabled=os.getenv("GOALSERVE_WS_ENABLED", default_ws_enabled).lower()
        in {"1", "true", "yes"},
        goalserve_ws_token_url=os.getenv("GOALSERVE_WS_TOKEN_URL", Settings.goalserve_ws_token_url),
        goalserve_ws_sport=os.getenv("GOALSERVE_WS_SPORT", Settings.goalserve_ws_sport),
        goalserve_widget_url_template=(
            os.getenv("GOALSERVE_WIDGET_URL_TEMPLATE", Settings.goalserve_widget_url_template or "")
            or None
        ),
        allsports_api_key=(
            os.getenv("ALLSPORTS_API_KEY")
            or os.getenv("ASA_API_KEY")
            or Settings.allsports_api_key
        ),
        allsports_http_url=os.getenv("ALLSPORTS_HTTP_URL", Settings.allsports_http_url),
        allsports_http_enabled=os.getenv("ALLSPORTS_HTTP_ENABLED", "0").lower()
        in {"1", "true", "yes"},
        allsports_ws_url=os.getenv("ALLSPORTS_WS_URL", Settings.allsports_ws_url),
        allsports_ws_enabled=os.getenv("ALLSPORTS_WS_ENABLED", "0").lower()
        in {"1", "true", "yes"},
        allsports_widget_url_template=(
            os.getenv("ALLSPORTS_WIDGET_URL_TEMPLATE")
            or os.getenv("ASA_WIDGET_URL_TEMPLATE")
            or Settings.allsports_widget_url_template
            or None
        ),
        auth_enabled=os.getenv("AUTH_ENABLED", "0").lower() in {"1", "true", "yes"},
        auth_rotation_days=int(os.getenv("AUTH_ROTATION_DAYS", Settings.auth_rotation_days)),
        auth_session_ttl_seconds=(
            int(os.getenv("AUTH_SESSION_TTL_SECONDS"))
            if os.getenv("AUTH_SESSION_TTL_SECONDS")
            else Settings.auth_session_ttl_seconds
        ),
        auth_password_length=int(os.getenv("AUTH_PASSWORD_LENGTH", Settings.auth_password_length)),
        auth_timezone=os.getenv("AUTH_TIMEZONE", Settings.auth_timezone),
        auth_cookie_name=os.getenv("AUTH_COOKIE_NAME", Settings.auth_cookie_name),
        auth_email_to=os.getenv("AUTH_EMAIL_TO", Settings.auth_email_to or "") or None,
        auth_email_subject=os.getenv("AUTH_EMAIL_SUBJECT", Settings.auth_email_subject),
        auth_notify_channel=os.getenv("AUTH_NOTIFY_CHANNEL", Settings.auth_notify_channel),
        smtp_host=os.getenv("SMTP_HOST", Settings.smtp_host or "") or None,
        smtp_port=int(os.getenv("SMTP_PORT", Settings.smtp_port)),
        smtp_security=os.getenv("SMTP_SECURITY", Settings.smtp_security),
        smtp_user=os.getenv("SMTP_USER", Settings.smtp_user or "") or None,
        smtp_password=os.getenv("SMTP_PASSWORD", Settings.smtp_password or "") or None,
        smtp_from=os.getenv("SMTP_FROM", Settings.smtp_from or "") or None,
        cors_origins=parsed_origins,
    )
