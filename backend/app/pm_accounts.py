"""Polymarket 实盘账户配置：只向前端暴露脱敏后的本地账户摘要。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any

import httpx
from py_clob_client_v2 import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, AssetType, BalanceAllowanceParams

from .config import Settings


@dataclass(frozen=True)
class PMAccountConfig:
    alias: str
    label: str
    host: str | None
    chain_id: int
    funder: str | None
    signature_type: int | None
    relayer_address: str | None
    private_key: str | None
    api_key: str | None
    api_secret: str | None
    api_passphrase: str | None
    has_private_key: bool
    has_api_credentials: bool
    has_relayer_api_key: bool
    live_trading_enabled: bool


def load_pm_account_configs(settings: Settings) -> list[PMAccountConfig]:
    """从 PM_ACCOUNTS_JSON 读取多个账户；密钥只用于构造客户端，不进入 API 响应。"""
    if not settings.pm_accounts_json:
        return []
    try:
        raw_accounts = json.loads(settings.pm_accounts_json)
    except json.JSONDecodeError as exc:
        raise ValueError("PM_ACCOUNTS_JSON must be a JSON array") from exc
    if not isinstance(raw_accounts, list):
        raise ValueError("PM_ACCOUNTS_JSON must be a JSON array")
    accounts: list[PMAccountConfig] = []
    seen_aliases: set[str] = set()
    for index, raw in enumerate(raw_accounts, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"PM account #{index} must be an object")
        alias = _clean_text(raw.get("alias"))
        if not alias:
            raise ValueError(f"PM account #{index} missing alias")
        if alias in seen_aliases:
            raise ValueError(f"duplicate PM account alias: {alias}")
        seen_aliases.add(alias)
        accounts.append(
            PMAccountConfig(
                alias=alias,
                label=_clean_text(raw.get("label")) or alias,
                host=_clean_text(raw.get("host")),
                chain_id=_int_or_default(raw.get("chain_id"), 137),
                funder=_clean_text(raw.get("funder")),
                signature_type=_optional_int(raw.get("signature_type")),
                relayer_address=_clean_text(raw.get("relayer_address")),
                private_key=_clean_text(raw.get("private_key")),
                api_key=_clean_text(raw.get("api_key")),
                api_secret=_clean_text(raw.get("api_secret")),
                api_passphrase=_clean_text(raw.get("api_passphrase")),
                has_private_key=bool(_clean_text(raw.get("private_key"))),
                has_api_credentials=all(
                    _clean_text(raw.get(key))
                    for key in ("api_key", "api_secret", "api_passphrase")
                ),
                has_relayer_api_key=bool(_clean_text(raw.get("relayer_api_key"))),
                live_trading_enabled=_truthy(raw.get("live_trading_enabled")),
            )
        )
    return accounts


def public_pm_accounts(settings: Settings) -> list[dict[str, Any]]:
    """返回前端下拉框可用的账户摘要，不包含任何 key/secret/passphrase。"""
    return [
        _public_pm_account_row(account)
        for account in load_pm_account_configs(settings)
    ]


async def public_pm_accounts_with_balances(settings: Settings) -> list[dict[str, Any]]:
    """返回账户摘要，并尽量用 PM CLOB 只读接口补充现金余额。

    查询失败时只返回脱敏错误，不影响前端下拉框展示。
    """
    accounts = load_pm_account_configs(settings)
    rows = [_public_pm_account_row(account) for account in accounts]
    queried = await asyncio.gather(
        *(
            asyncio.to_thread(_query_pm_account_funds, account)
            if account.live_trading_enabled
            else _async_none()
            for account in accounts
        )
    )
    for row, result in zip(rows, queried, strict=False):
        if not result:
            continue
        row.update(result)
    position_groups = await asyncio.gather(
        *(
            asyncio.to_thread(_query_pm_data_positions, account, settings.pm_data_api_url, 500)
            if account.live_trading_enabled
            else _async_list()
            for account in accounts
        ),
        return_exceptions=True,
    )
    for row, positions in zip(rows, position_groups, strict=False):
        if isinstance(positions, Exception):
            row["position_error"] = _safe_error(positions)
            continue
        if not positions:
            continue
        position_funds = round(sum(_number(position.get("currentValue")) for position in positions), 8)
        available_funds = _number(row.get("available_funds"))
        row["position_count"] = len(positions)
        row["position_funds"] = position_funds
        row["total_funds"] = round(available_funds + position_funds, 8)
    return rows


async def _async_none() -> None:
    return None


async def _async_list() -> list[Any]:
    return []


async def public_pm_positions(settings: Settings, account_alias: str | None = None) -> list[dict[str, Any]]:
    """读取 PM Data API 当前持仓；失败时返回空列表，避免拖垮主界面。"""

    accounts = _select_accounts(load_pm_account_configs(settings), account_alias)
    groups = await asyncio.gather(
        *(asyncio.to_thread(_query_pm_data_positions, account, settings.pm_data_api_url, 500) for account in accounts),
        return_exceptions=True,
    )
    rows: list[dict[str, Any]] = []
    for account, group in zip(accounts, groups, strict=False):
        if isinstance(group, Exception):
            continue
        for raw in group:
            normalized = _normalize_pm_position(account, raw)
            if normalized:
                rows.append(normalized)
    return rows


async def public_pm_trades(
    settings: Settings,
    account_alias: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """读取 PM Data API 用户成交；这里只做格式映射，不写 Redis。"""

    accounts = _select_accounts(load_pm_account_configs(settings), account_alias)
    groups = await asyncio.gather(
        *(asyncio.to_thread(_query_pm_data_trades, account, settings.pm_data_api_url, limit) for account in accounts),
        return_exceptions=True,
    )
    rows: list[dict[str, Any]] = []
    for account, group in zip(accounts, groups, strict=False):
        if isinstance(group, Exception):
            continue
        for raw in group:
            normalized = _normalize_pm_trade(account, raw)
            if normalized:
                rows.append(normalized)
    return rows


def _public_pm_account_row(account: PMAccountConfig) -> dict[str, Any]:
    return {
        "id": account.alias,
        "name": account.label,
        "host": account.host,
        "chain_id": account.chain_id,
        "funder_configured": bool(account.funder),
        "private_key_configured": account.has_private_key,
        "api_credentials_configured": account.has_api_credentials,
        "relayer_configured": bool(account.relayer_address and account.has_relayer_api_key),
        "live_trading_enabled": account.live_trading_enabled,
        "total_funds": 0.0,
        "position_funds": 0.0,
        "available_funds": 0.0,
    }


def _query_pm_account_funds(account: PMAccountConfig) -> dict[str, Any] | None:
    if not (account.private_key and account.has_api_credentials and account.api_key and account.api_secret and account.api_passphrase):
        return None
    try:
        client = ClobClient(
            account.host or "https://clob.polymarket.com",
            key=account.private_key,
            chain_id=account.chain_id,
            creds=ApiCreds(account.api_key, account.api_secret, account.api_passphrase),
            signature_type=account.signature_type,
            funder=account.funder,
        )
        balance = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        amount = _usdc_amount(_first_present(balance, "balance", "collateral", "cash"))
        return {
            "total_funds": amount,
            "position_funds": 0.0,
            "available_funds": amount,
        }
    except Exception as exc:
        return {"balance_error": _safe_error(exc)}


def _query_pm_data_positions(account: PMAccountConfig, base_url: str, limit: int) -> list[dict[str, Any]]:
    address = _pm_profile_address(account)
    if not address:
        return []
    response = httpx.get(
        f"{base_url.rstrip('/')}/positions",
        params={"user": address, "limit": limit, "sizeThreshold": 0},
        timeout=5.0,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def _query_pm_data_trades(account: PMAccountConfig, base_url: str, limit: int) -> list[dict[str, Any]]:
    address = _pm_profile_address(account)
    if not address:
        return []
    response = httpx.get(
        f"{base_url.rstrip('/')}/trades",
        params={"user": address, "limit": limit},
        timeout=5.0,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def _normalize_pm_position(account: PMAccountConfig, row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    asset = _clean_text(row.get("asset")) or _clean_text(row.get("conditionId")) or ""
    outcome = _clean_text(row.get("outcome")) or _outcome_from_index(row.get("outcomeIndex"))
    size = _number(row.get("size"))
    avg_price = _number(row.get("avgPrice"))
    current_price = _number(row.get("curPrice"))
    return {
        "source": "pm",
        "account_alias": account.alias,
        "id": asset or f"{account.alias}:{row.get('slug') or ''}:{outcome}",
        "order_id": (asset or f"{account.alias}:{row.get('slug') or ''}:{outcome}")[:16],
        "condition_id": row.get("conditionId"),
        "asset_id": asset,
        "slug": row.get("slug") or row.get("eventSlug") or "",
        "outcome_key": _normalize_outcome_key(outcome),
        "team_label": _outcome_label(outcome),
        "team_name": _clean_text(row.get("outcome")) or "",
        "shares": size,
        "avg_entry_price": avg_price,
        "cost_basis": _number(row.get("initialValue")) or size * avg_price,
        "current_bid1": current_price,
        "current_ask1": current_price,
        "unrealized_pnl": _number(row.get("cashPnl")),
        "ts_utc": _timestamp_to_utc(row.get("updatedAt") or row.get("timestamp")),
        "raw": row,
    }


def _normalize_pm_trade(account: PMAccountConfig, row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    side = str(row.get("side") or "").strip().lower()
    if side not in {"buy", "sell"}:
        side = "buy" if side == "BUY".lower() else side
    price = _number(row.get("price"))
    shares = _number(row.get("size"))
    order_id = _clean_text(row.get("transactionHash")) or _clean_text(row.get("id")) or ""
    outcome = _clean_text(row.get("outcome")) or _outcome_from_index(row.get("outcomeIndex"))
    return {
        "source": "pm",
        "account_alias": account.alias,
        "order_id": (order_id or f"{account.alias}:{row.get('timestamp') or ''}:{row.get('asset') or ''}")[:16],
        "side": side,
        "condition_id": row.get("conditionId"),
        "asset_id": row.get("asset"),
        "slug": row.get("slug") or row.get("eventSlug") or "",
        "outcome_key": _normalize_outcome_key(outcome),
        "team_label": _outcome_label(outcome),
        "team_name": _clean_text(row.get("outcome")) or "",
        "shares": shares,
        "price": price,
        "amount_usd": round(shares * price, 8),
        "ts_utc": _timestamp_to_utc(row.get("timestamp")),
        "raw": row,
    }


def _select_accounts(accounts: list[PMAccountConfig], account_alias: str | None) -> list[PMAccountConfig]:
    return [
        account
        for account in accounts
        if account.live_trading_enabled and (account_alias is None or account.alias == account_alias)
    ]


def _pm_profile_address(account: PMAccountConfig) -> str | None:
    return account.funder or account.relayer_address


def _first_present(row: Any, *keys: str) -> Any:
    if not isinstance(row, dict):
        return None
    for key in keys:
        value = row.get(key)
        if value not in {None, ""}:
            return value
    return None


def _usdc_amount(value: Any) -> float:
    try:
        raw = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return raw / 1_000_000 if raw > 1_000_000 else raw


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _timestamp_to_utc(value: Any) -> str:
    if value in {None, ""}:
        return ""
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _outcome_from_index(value: Any) -> str:
    try:
        index = int(value)
    except (TypeError, ValueError):
        return ""
    return {0: "home", 1: "away", 2: "draw"}.get(index, "")


def _normalize_outcome_key(value: str | None) -> str:
    lower = str(value or "").strip().lower()
    if lower in {"home", "away", "draw"}:
        return lower
    return lower


def _outcome_label(value: str | None) -> str:
    lower = _normalize_outcome_key(value)
    if lower == "home":
        return "Home"
    if lower == "away":
        return "Away"
    if lower == "draw":
        return "Draw"
    return str(value or "")


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    for marker in ("api_key", "api_secret", "api_passphrase", "private_key", "secret", "passphrase"):
        text = text.replace(marker, "[REDACTED]")
    return text[:160]


def has_pm_account(settings: Settings, alias: str | None) -> bool:
    if not alias:
        return False
    return any(account.alias == alias for account in load_pm_account_configs(settings))


def _clean_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return _int_or_default(value, 0)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
