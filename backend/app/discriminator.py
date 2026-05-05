"""比赛判别器：只负责变化识别、比赛过程日志和交易员事件分发。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .timeseries import MATCH_RELATED_TTL_SECONDS


PROCESS_LOG_FIELDS = {
    "score_home",
    "score_away",
    "status",
    "red_cards",
    "yellow_cards",
    "penalties",
    "free_kicks",
    "corners",
    "shots_on_target",
}

DISPLAY_PROCESS_LOG_FIELDS = {
    "score_home",
    "score_away",
    "status",
    "penalties",
}


class MatchDiscriminator:
    """把 Listener 写入后的新旧状态转成业务事件。

    Listener 不在这里之外判断“比分是否变化/是否红黄牌”等业务含义；
    它只把 source、guid、previous/current 状态交给判别器。
    """

    def __init__(self, *, store: Any, broadcaster: Any | None = None, trader_manager: Any | None = None):
        self._store = store
        self._broadcaster = broadcaster
        self._trader_manager = trader_manager
        self._external_process_values: dict[tuple[str, str], dict[str, Any]] = {}

    async def process_market_tick(
        self,
        *,
        source: str,
        guid: str,
        payload: dict[str, Any],
        previous: dict[str, Any],
        current: dict[str, Any],
        mapping: dict[str, str],
    ) -> dict[str, Any] | None:
        changed = _changed_fields(previous, current, mapping)
        if not changed:
            return None
        event = await self._standard_event(source, guid, payload, changed, current, previous)
        self._enqueue_trader(event)
        await self._write_standard_event(event)
        return event

    async def process_external_state(
        self,
        *,
        source: str,
        guid: str,
        payload: dict[str, Any],
        previous: dict[str, Any],
        current: dict[str, Any],
        mapping: dict[str, str],
        ws_message: dict[str, Any],
    ) -> dict[str, Any] | None:
        memory_key = (source, guid)
        previous_values = self._external_process_values.get(memory_key)
        if previous_values is None:
            previous_values = _observed_values(previous, mapping)
        current_values = _observed_values(current, mapping)
        changed = _changed_observed_fields(previous_values, current_values)
        self._external_process_values[memory_key] = {**previous_values, **current_values}
        if not changed:
            return None
        previous_for_event = {**previous, **previous_values}
        event = await self._standard_event(source, guid, payload, changed, current, previous_for_event)
        self._enqueue_trader(event)
        await self._write_standard_event(event)
        await self._write_process_logs(source, guid, previous_for_event, current, changed)
        if self._broadcaster is not None:
            await self._broadcaster.publish(ws_message)
        return event

    async def _standard_event(
        self,
        source: str,
        guid: str,
        payload: dict[str, Any],
        changed_fields: list[str],
        state: dict[str, Any],
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        now = _utc_now()
        event = {
            "received_at_utc": now,
            "pushed_at_utc": now,
            "source": source,
            "guid": guid,
            "score_home": state.get("score_home"),
            "score_away": state.get("score_away"),
            "previous_score_home": previous.get("score_home"),
            "previous_score_away": previous.get("score_away"),
            "pm_score_home_at_event": payload.get("pm_score_home_at_event"),
            "pm_score_away_at_event": payload.get("pm_score_away_at_event"),
            "match_time": state.get("match_time"),
            "period": state.get("period"),
            "clock": state.get("clock"),
            "red_cards": state.get("red_cards"),
            "yellow_cards": state.get("yellow_cards"),
            "substitutions": state.get("substitutions"),
            "var_events": state.get("var_events"),
            "penalties": state.get("penalties"),
            "free_kicks": state.get("free_kicks"),
            "moneyline": _moneyline(state),
            "changed_fields": changed_fields,
            "raw_ref": payload.get("message_id"),
        }
        return event

    async def _write_standard_event(self, event: dict[str, Any]) -> None:
        await self._store.add_stream("stream:standard_events", event, ttl_seconds=MATCH_RELATED_TTL_SECONDS)

    async def _write_process_logs(
        self,
        source: str,
        guid: str,
        previous: dict[str, Any],
        current: dict[str, Any],
        changed_fields: list[str],
    ) -> None:
        wrote_score_change = False
        for field in changed_fields:
            if field not in DISPLAY_PROCESS_LOG_FIELDS:
                continue
            if not _should_write_process_log(source, field, current):
                continue
            if field in {"score_home", "score_away"}:
                if wrote_score_change:
                    continue
                if not _has_complete_score(current):
                    continue
                if not _has_complete_score(previous) and _is_zero_zero_score(current):
                    continue
                wrote_score_change = True
            row = {
                "ts_utc": current.get("updated_at_utc") or _utc_now(),
                "guid": guid,
                "source": "discriminator",
                "data_source": source,
                "event_kind": _event_kind(field),
                "message": _event_message(field, previous, current),
                "field": field,
                "previous": previous.get(field),
                "current": current.get(field),
            }
            await self._store.add_stream("stream:match_logs", row, ttl_seconds=MATCH_RELATED_TTL_SECONDS)
            if self._broadcaster is not None:
                await self._broadcaster.publish({"topic": "match.log", "payload": row})

    def _enqueue_trader(self, event: dict[str, Any]) -> None:
        if self._trader_manager is not None:
            if hasattr(self._trader_manager, "on_match_signal"):
                self._trader_manager.on_match_signal(event)
            else:
                self._trader_manager.enqueue_event(event)


def _changed_fields(previous: dict[str, Any], current: dict[str, Any], mapping: dict[str, str]) -> list[str]:
    changed: list[str] = []
    for key, label in mapping.items():
        if label in PROCESS_LOG_FIELDS and not _has_meaningful_value(current.get(key)):
            continue
        if previous.get(key) != current.get(key):
            changed.append(label)
    return changed


def _observed_values(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, label in mapping.items():
        value = row.get(key)
        if _has_meaningful_value(value):
            values[label] = value
    return values


def _changed_observed_fields(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for label, value in current.items():
        if previous.get(label) != value:
            changed.append(label)
    return changed


def _moneyline(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "home": {"ask1": state.get("home_ask1"), "bid1": state.get("home_bid1")},
        "draw": {"ask1": state.get("draw_ask1"), "bid1": state.get("draw_bid1")},
        "away": {"ask1": state.get("away_ask1"), "bid1": state.get("away_bid1")},
    }


def _event_kind(field: str) -> str:
    if field in {"score_home", "score_away"}:
        return "score_changed"
    return f"{field}_changed"


def _event_message(field: str, previous: dict[str, Any], current: dict[str, Any]) -> str:
    if field in {"score_home", "score_away"}:
        after = f"{current.get('score_home', '-')}-{current.get('score_away', '-')}"
        if not _has_complete_score(previous):
            return f"比分 {after}"
        before = f"{previous.get('score_home', '-')}-{previous.get('score_away', '-')}"
        return f"比分变化 {before} -> {after}"
    labels = {
        "status": "比赛状态",
        "red_cards": "红牌",
        "yellow_cards": "黄牌",
        "penalties": "点球",
        "free_kicks": "任意球",
        "corners": "角球",
        "shots_on_target": "射正",
    }
    label = labels.get(field, field)
    return f"{label} {_format_current_value(current.get(field))}"


def _should_write_process_log(source: str, field: str, current: dict[str, Any]) -> bool:
    if field != "status":
        return True
    if source != "pm_sports":
        return False
    return str(current.get("status") or "").strip().lower() in {"live", "finished", "ended", "closed"}


def _has_meaningful_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple, set)) and not value:
        return False
    if isinstance(value, dict) and not value:
        return False
    return True


def _has_complete_score(row: dict[str, Any]) -> bool:
    return row.get("score_home") is not None and row.get("score_away") is not None


def _is_zero_zero_score(row: dict[str, Any]) -> bool:
    return row.get("score_home") == 0 and row.get("score_away") == 0


def _format_current_value(value: Any) -> str:
    if isinstance(value, dict) and "home" in value and "away" in value:
        return f"{value.get('home', '-')}-{value.get('away', '-')}"
    return str(value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
