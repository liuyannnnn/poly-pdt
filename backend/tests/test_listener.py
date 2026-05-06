import asyncio
from datetime import UTC, datetime

import pytest

from app.collector import Collector, StaticGSHttpClient, StaticPMHttpClient
from app.discriminator import MatchDiscriminator
from app.listener import BroadcastHub, Listener
from app.store import MemoryStore
from app.timeseries import MATCH_RELATED_TTL_SECONDS
from app.trader import TraderManager

from .fixtures import GS_D1, GS_HOME, PM_EVENTS


async def seed_store() -> tuple[MemoryStore, str]:
    store = MemoryStore()
    report = await Collector(
        store=store,
        pm_client=StaticPMHttpClient(PM_EVENTS[:1]),
        gs_client=StaticGSHttpClient(home=GS_HOME, d1=GS_D1),
    ).collect_once()
    return store, report["bindings"][0]["guid"]


class SlowStreamStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.stream_started = asyncio.Event()
        self.allow_stream = asyncio.Event()

    async def add_stream(self, key, value, max_len=10_000, ttl_seconds=None):
        self.stream_started.set()
        await self.allow_stream.wait()
        await super().add_stream(key, value, max_len=max_len, ttl_seconds=ttl_seconds)


class RecordingTraderManager:
    def __init__(self):
        self.events = []
        self.market_ticks = []
        self.account_events = []

    def enqueue_event(self, event):
        self.events.append(event)

    def on_match_signal(self, event):
        self.events.append(event)

    async def on_market_tick(self, event):
        self.market_ticks.append(event)

    async def on_account_event(self, event):
        self.account_events.append(event)


class RecordingResampler:
    def __init__(self):
        self.rows = []

    def observe_tick(self, row):
        self.rows.append(row)


@pytest.mark.asyncio
async def test_broadcast_hub_fans_out_messages_to_each_subscriber():
    hub = BroadcastHub()
    first = hub.subscribe()
    second = hub.subscribe()

    try:
        await hub.publish({"topic": "market.tick", "payload": {"match_id": "guid-1"}})

        assert await first.drain() == [{"topic": "market.tick", "payload": {"match_id": "guid-1"}}]
        assert await second.drain() == [{"topic": "market.tick", "payload": {"match_id": "guid-1"}}]
        assert await hub.drain() == [{"topic": "market.tick", "payload": {"match_id": "guid-1"}}]
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_broadcast_hub_keeps_only_bounded_debug_history():
    hub = BroadcastHub(history_limit=2)
    subscription = hub.subscribe()

    try:
        await hub.publish({"topic": "one"})
        await hub.publish({"topic": "two"})
        await hub.publish({"topic": "three"})

        assert await subscription.drain() == [{"topic": "one"}, {"topic": "two"}, {"topic": "three"}]
        assert await hub.drain() == [{"topic": "two"}, {"topic": "three"}]
    finally:
        subscription.close()


@pytest.mark.asyncio
async def test_discriminator_enqueues_trader_event_before_slow_stream_logging_finishes():
    store = SlowStreamStore()
    trader = RecordingTraderManager()
    discriminator = MatchDiscriminator(store=store, trader_manager=trader)

    task = asyncio.create_task(
        discriminator.process_external_state(
            source="asa_live",
            guid="guid-1",
            payload={"message_id": "asa-score-1", "pm_score_home_at_event": 0, "pm_score_away_at_event": 0},
            previous={"score_home": 0, "score_away": 0},
            current={"score_home": 1, "score_away": 0, "updated_at_utc": "2026-05-05T01:00:00Z"},
            mapping={"score_home": "score_home", "score_away": "score_away"},
            ws_message={"topic": "external.match", "payload": {"guid": "guid-1"}},
        )
    )
    try:
        await asyncio.wait_for(store.stream_started.wait(), timeout=0.2)
        await asyncio.sleep(0)
        assert len(trader.events) == 1
        assert trader.events[0]["score_home"] == 1
    finally:
        store.allow_stream.set()
        await task


@pytest.mark.asyncio
async def test_listener_updates_pm_market_without_overwriting_gs_state_and_broadcasts():
    store, guid = await seed_store()
    pm = await store.get_json(f"pm:match:{guid}")
    await store.set_json(f"pm:match:{guid}", {**pm, "status": "live"})
    hub = BroadcastHub()
    trader = RecordingTraderManager()
    resampler = RecordingResampler()
    listener = Listener(store=store, broadcaster=hub, trader_manager=trader, timeseries_resampler=resampler)
    tick_ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    event = await listener.process_payload(
        "pm_market",
        {
            "asset_id": "asset-ars-home",
            "bid": 0.51,
            "ask": 0.53,
            "ts": tick_ts,
            "message_id": "market-1",
        },
    )

    pm_match = await store.get_json(f"pm:match:{guid}")
    gs_match = await store.get_json(f"gs:match:{guid}")
    orderbook = await store.get_json(f"orderbook:{guid}:home")
    ticks = await store.get_json(f"series:pm:ticks:{guid}")
    broadcasts = await hub.drain()

    assert event is not None
    assert event["source"] == "pm_market"
    assert event["guid"] == guid
    assert event["event_type"] == "market_tick"
    assert event["outcome_key"] == "home"
    assert event["ask1"] == 0.53
    assert event["bid1"] == 0.51
    assert trader.market_ticks == [event]
    assert trader.events == []
    assert await store.stream("stream:standard_events") == []
    assert pm_match["home_ask1"] == 0.53
    assert gs_match["odds_home"] == 2.1
    assert orderbook["ask1"] == 0.53
    assert ticks[-1]["phase"] == "LIVE"
    assert ticks[-1]["snapshot_ts_utc"] == tick_ts
    assert ticks[-1]["home_ask"] == 0.53
    assert ticks[-1]["draw_ask"] == 0.27
    assert ticks[-1]["away_ask"] == 0.33
    assert resampler.rows[-1]["snapshot_ts_utc"] == tick_ts
    assert resampler.rows[-1]["home_ask"] == 0.53
    assert await store.ttl(f"series:pm:ticks:{guid}") == MATCH_RELATED_TTL_SECONDS
    assert broadcasts[-1]["topic"] == "market.tick"
    assert broadcasts[-1]["payload"]["match_id"] == guid
    assert broadcasts[-1]["payload"]["outcome"] == "home"


@pytest.mark.asyncio
async def test_listener_ignores_pm_market_tick_until_match_is_live():
    store, guid = await seed_store()
    hub = BroadcastHub()
    listener = Listener(store=store, broadcaster=hub, trader_manager=TraderManager(store=store))
    previous = await store.get_json(f"pm:match:{guid}")

    event = await listener.process_payload(
        "pm_market",
        {
            "asset_id": "asset-ars-home",
            "bid": 0.51,
            "ask": 0.53,
            "ts": "2026-05-01T18:58:00Z",
            "message_id": "market-pre-1",
        },
    )

    pm_match = await store.get_json(f"pm:match:{guid}")

    assert event is None
    assert pm_match["home_ask1"] == previous["home_ask1"]
    assert await store.get_json(f"series:pm:ticks:{guid}") is None
    assert await store.get_json(f"orderbook:{guid}:home") is None
    assert await hub.drain() == []


@pytest.mark.asyncio
async def test_listener_ignores_pm_market_tick_after_match_has_been_finished_for_15_minutes():
    store, guid = await seed_store()
    pm = await store.get_json(f"pm:match:{guid}")
    await store.set_json(
        f"pm:match:{guid}",
        {
            **pm,
            "status": "finished",
            "updated_at_utc": "2026-05-01T21:00:00Z",
        },
    )
    hub = BroadcastHub()
    listener = Listener(store=store, broadcaster=hub, trader_manager=TraderManager(store=store))

    event = await listener.process_payload(
        "pm_market",
        {
            "asset_id": "asset-ars-home",
            "bid": 0.01,
            "ask": 0.02,
            "ts": "2026-05-01T21:16:00Z",
            "message_id": "market-after-finished-1",
        },
    )

    assert event is None
    assert await store.get_json(f"series:pm:ticks:{guid}") is None
    assert await hub.drain() == []


@pytest.mark.asyncio
async def test_listener_marks_pm_match_finished_when_external_source_finishes():
    store, guid = await seed_store()
    pm = await store.get_json(f"pm:match:{guid}")
    await store.set_json(f"pm:match:{guid}", {**pm, "status": "live", "match_time": "2H 84"})
    await store.set_text("idx:asa:id:asa-100", guid)
    listener = Listener(store=store, broadcaster=BroadcastHub(), trader_manager=TraderManager(store=store))

    event = await listener.process_payload(
        "asa_live",
        {
            "match_id": "asa-100",
            "score": {"home": 1, "away": 0},
            "status": "finished",
            "match_time": "Finished",
            "ts": "2026-05-01T20:55:00Z",
            "message_id": "asa-finished-1",
        },
    )

    pm_match = await store.get_json(f"pm:match:{guid}")
    assert pm_match["status"] == "finished"
    assert pm_match["finished_at_utc"] == "2026-05-01T20:55:00Z"
    assert pm_match["status_source"] == "asa_live"


@pytest.mark.asyncio
async def test_listener_updates_gs_score_without_overwriting_pm_score():
    store, guid = await seed_store()
    listener = Listener(store=store, broadcaster=BroadcastHub(), trader_manager=TraderManager(store=store))

    event = await listener.process_payload(
        "gs_live",
        {
            "inplay_id": "live-100",
            "score": {"home": 1, "away": 0},
            "clock": "34:12",
            "period": "1H",
            "red_cards": {"home": 0, "away": 0},
            "message_id": "gs-1",
        },
    )

    pm_match = await store.get_json(f"pm:match:{guid}")
    gs_match = await store.get_json(f"gs:match:{guid}")

    assert event is not None
    assert event["source"] == "gs_live"
    assert event["score_home"] == 1
    assert "score_home" in event["changed_fields"]
    assert gs_match["score_home"] == 1
    assert gs_match["clock"] == "34:12"
    assert pm_match["score_home"] == 0


@pytest.mark.asyncio
async def test_listener_updates_asa_score_without_overwriting_pm_score():
    store, guid = await seed_store()
    await store.set_json(
        f"asa:match:{guid}",
        {"source": "asa", "match_id": "asa-100", "inplay_id": "asa-100", "score_home": 0, "score_away": 0},
    )
    await store.set_text("idx:asa:inplay:asa-100", guid)
    listener = Listener(store=store, broadcaster=BroadcastHub(), trader_manager=TraderManager(store=store))

    event = await listener.process_payload(
        "asa_live",
        {
            "event_key": "asa-100",
            "event_status": "35",
            "event_final_result": "1 - 0",
            "message_id": "asa-1",
        },
    )

    pm_match = await store.get_json(f"pm:match:{guid}")
    asa_match = await store.get_json(f"asa:match:{guid}")

    assert event is not None
    assert event["source"] == "asa_live"
    assert event["score_home"] == 1
    assert event["pm_score_home_at_event"] == 0
    assert event["pm_score_away_at_event"] == 0
    assert "score_home" in event["changed_fields"]
    assert asa_match["score_home"] == 1
    assert asa_match["clock"] == "35"
    assert pm_match["score_home"] == 0


@pytest.mark.asyncio
async def test_listener_broadcasts_external_match_even_when_values_are_unchanged():
    store, guid = await seed_store()
    await store.set_json(
        f"asa:match:{guid}",
        {
            "source": "asa",
            "guid": guid,
            "match_id": "asa-100",
            "inplay_id": "asa-100",
            "score_home": 1,
            "score_away": 0,
            "clock": "35",
            "updated_at_utc": "2026-05-03T12:00:00Z",
        },
    )
    await store.set_text("idx:asa:inplay:asa-100", guid)
    hub = BroadcastHub()
    listener = Listener(store=store, broadcaster=hub, trader_manager=TraderManager(store=store))

    event = await listener.process_payload(
        "asa_live",
        {
            "event_key": "asa-100",
            "event_status": "35",
            "event_final_result": "1 - 0",
            "message_id": "asa-same-1",
            "ts": "2026-05-03T12:00:05Z",
        },
    )

    broadcasts = await hub.drain()
    external_messages = [row for row in broadcasts if row.get("topic") == "external.match"]
    asa_match = await store.get_json(f"asa:match:{guid}")

    assert event is None
    assert asa_match["updated_at_utc"] == "2026-05-03T12:00:05Z"
    assert external_messages
    assert external_messages[-1]["payload"]["guid"] == guid
    assert external_messages[-1]["payload"]["score_home"] == 1
    assert external_messages[-1]["payload"]["clock"] == "35"


@pytest.mark.asyncio
async def test_listener_infers_asa_score_from_goal_events_when_top_level_score_is_missing():
    store, guid = await seed_store()
    await store.set_json(
        f"asa:match:{guid}",
        {"source": "asa", "match_id": "asa-100", "inplay_id": "asa-100", "score_home": 1, "score_away": 0},
    )
    await store.set_text("idx:asa:inplay:asa-100", guid)
    listener = Listener(store=store, broadcaster=BroadcastHub(), trader_manager=TraderManager(store=store))

    event = await listener.process_payload(
        "asa_live",
        {
            "event_key": "asa-100",
            "event_status": "32",
            "goalscorers": [
                {"time": "10", "score": "1 - 0", "home_scorer": "A"},
                {"time": "32", "score": "2 - 0", "home_scorer": "B", "info": "Penalty"},
            ],
            "message_id": "asa-goal-events-1",
        },
    )

    asa_match = await store.get_json(f"asa:match:{guid}")
    match_logs = await store.stream("stream:match_logs")

    assert event is not None
    assert "score_home" in event["changed_fields"]
    assert asa_match["score_home"] == 2
    assert asa_match["score_away"] == 0
    assert match_logs[0]["event_kind"] == "score_changed"
    assert match_logs[0]["message"] == "比分变化 1-0 -> 2-0"


@pytest.mark.asyncio
async def test_listener_ignores_pm_sports_score_regression_without_triggering_trader():
    store, guid = await seed_store()
    pm = await store.get_json(f"pm:match:{guid}")
    pm.update({"status": "live", "score_home": 1, "score_away": 0, "match_time": "1H 20:00"})
    await store.set_json(f"pm:match:{guid}", pm)
    trader = TraderManager(store=store)
    created = await trader.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await trader.start_trading(created.trading_id)
    listener = Listener(store=store, broadcaster=BroadcastHub(), trader_manager=trader)

    event = await listener.process_payload(
        "pm_sports",
        {
            "event_id": "pm-ars-che",
            "status": "live",
            "score": {"home": 0, "away": 0},
            "match_time": "1H 21:00",
            "message_id": "sports-regression-1",
        },
    )

    updated = await store.get_json(f"pm:match:{guid}")
    dead_letters = await store.stream("stream:dead_letters")

    assert event is not None
    assert "score_home" not in event["changed_fields"]
    assert updated["score_home"] == 1
    assert updated["score_away"] == 0
    assert updated["match_time"] == "1H 21:00"
    assert trader.queue_size(created.trading_id) == 1
    assert dead_letters[-1]["reason"] == "score_regression"


@pytest.mark.asyncio
async def test_listener_processes_already_normalized_asa_ws_payload_without_losing_score():
    store, guid = await seed_store()
    await store.set_json(
        f"asa:match:{guid}",
        {"source": "asa", "match_id": "asa-100", "inplay_id": "asa-100", "score_home": 0, "score_away": 0},
    )
    await store.set_text("idx:asa:id:asa-100", guid)
    listener = Listener(store=store, broadcaster=BroadcastHub(), trader_manager=TraderManager(store=store))

    event = await listener.process_payload(
        "asa_live",
        {
            "source": "asa",
            "match_id": "asa-100",
            "inplay_id": "asa-100",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "start_time_utc": "2026-05-01T19:00:00Z",
            "score": {"home": 1, "away": 0},
            "score_home": 1,
            "score_away": 0,
            "corners": {"home": 8, "away": 3},
            "clock": "12",
            "period": "1H",
        },
    )

    asa_match = await store.get_json(f"asa:match:{guid}")
    match_logs = await store.stream("stream:match_logs")

    assert event is not None
    assert asa_match["score_home"] == 1
    assert asa_match["score_away"] == 0
    assert asa_match["corners"] == {"home": 8, "away": 3}
    assert [row["message"] for row in match_logs] == ["比分变化 0-0 -> 1-0"]


@pytest.mark.asyncio
async def test_listener_keeps_previous_asa_values_when_ws_payload_omits_fields():
    store, guid = await seed_store()
    await store.set_json(
        f"asa:match:{guid}",
        {
            "source": "asa",
            "match_id": "asa-100",
            "inplay_id": "asa-100",
            "score_home": 3,
            "score_away": 0,
            "yellow_cards": {"home": 0, "away": 1},
            "corners": {"home": 1, "away": 0},
        },
    )
    await store.set_text("idx:asa:inplay:asa-100", guid)
    listener = Listener(store=store, broadcaster=BroadcastHub(), trader_manager=TraderManager(store=store))

    event = await listener.process_payload(
        "asa_live",
        {
            "event_key": "asa-100",
            "statistics": [{"type": "On Target", "home": "9", "away": "1"}],
            "message_id": "asa-partial-1",
        },
    )

    asa_match = await store.get_json(f"asa:match:{guid}")
    match_logs = await store.stream("stream:match_logs")

    assert event is not None
    assert asa_match["score_home"] == 3
    assert asa_match["score_away"] == 0
    assert asa_match["yellow_cards"] == {"home": 0, "away": 1}
    assert asa_match["corners"] == {"home": 1, "away": 0}
    assert asa_match["shots_on_target"] == {"home": 9, "away": 1}
    assert match_logs == []


@pytest.mark.asyncio
async def test_discriminator_writes_match_process_logs_for_external_match_changes():
    store, guid = await seed_store()
    trader = TraderManager(store=store)
    created = await trader.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 500},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await trader.start_trading(created.trading_id)
    listener = Listener(store=store, broadcaster=BroadcastHub(), trader_manager=trader)

    await listener.process_payload(
        "gs_live",
        {
            "inplay_id": "live-100",
            "score": {"home": 1, "away": 0},
            "clock": "34:12",
            "period": "1H",
            "red_cards": {"home": 1, "away": 0},
            "message_id": "gs-process-1",
        },
    )

    match_logs = await store.stream("stream:match_logs")
    broadcasts = await listener._broadcaster.drain()

    assert trader.queue_size(created.trading_id) == 1
    assert [row["event_kind"] for row in match_logs] == ["score_changed"]
    assert match_logs[0]["guid"] == guid
    assert "比分变化" in match_logs[0]["message"]
    assert any(message.get("topic") == "match.log" for message in broadcasts)


@pytest.mark.asyncio
async def test_discriminator_logs_penalties_but_not_regular_stat_changes():
    store, guid = await seed_store()
    await store.set_json(
        f"asa:match:{guid}",
        {"source": "asa", "match_id": "asa-100", "inplay_id": "asa-100"},
    )
    await store.set_text("idx:asa:inplay:asa-100", guid)
    listener = Listener(store=store, broadcaster=BroadcastHub(), trader_manager=TraderManager(store=store))

    await listener.process_payload(
        "asa_live",
        {
            "event_key": "asa-100",
            "event_status": "32",
            "goalscorers": [
                {"time": "32", "score": "0 - 0", "home_scorer": "A", "info": "Penalty"},
            ],
            "statistics": [{"type": "On Target", "home": "2", "away": "1"}],
            "message_id": "asa-process-penalty-1",
        },
    )

    match_logs = await store.stream("stream:match_logs")

    assert [row["event_kind"] for row in match_logs] == ["penalties_changed"]
    assert match_logs[0]["guid"] == guid
    assert match_logs[0]["message"].startswith("点球 ")


@pytest.mark.asyncio
async def test_discriminator_does_not_log_duplicate_external_values_from_memory():
    store, guid = await seed_store()
    listener = Listener(store=store, broadcaster=BroadcastHub(), trader_manager=TraderManager(store=store))

    await listener._discriminator.process_external_state(
        source="asa_live",
        guid=guid,
        payload={"message_id": "asa-1"},
        previous={},
        current={"score_home": 1, "score_away": 0, "updated_at_utc": "2026-05-03T12:00:01Z"},
        mapping={"score_home": "score_home", "score_away": "score_away"},
        ws_message={"topic": "trader.event", "payload": {"event_id": guid}},
    )
    await listener._discriminator.process_external_state(
        source="asa_live",
        guid=guid,
        payload={"message_id": "asa-2"},
        previous={},
        current={"score_home": 1, "score_away": 0, "updated_at_utc": "2026-05-03T12:00:10Z"},
        mapping={"score_home": "score_home", "score_away": "score_away"},
        ws_message={"topic": "trader.event", "payload": {"event_id": guid}},
    )

    match_logs = await store.stream("stream:match_logs")
    standard_events = await store.stream("stream:standard_events")

    assert [row["message"] for row in match_logs] == ["比分 1-0"]
    assert len(standard_events) == 1


@pytest.mark.asyncio
async def test_discriminator_logs_score_change_once_when_both_scores_change():
    store = MemoryStore()
    listener = Listener(store=store, broadcaster=BroadcastHub())

    await listener._discriminator.process_external_state(
        source="gs_ws",
        guid="guid-match-1",
        payload={},
        previous={"score_home": 0, "score_away": 0},
        current={
            "score_home": 1,
            "score_away": 1,
            "updated_at_utc": "2026-05-02T14:30:00Z",
        },
        mapping={"score_home": "score_home", "score_away": "score_away"},
        ws_message={"type": "match.update", "match": {"guid": "guid-match-1"}},
    )

    match_logs = await store.stream("stream:match_logs")

    assert [row["event_kind"] for row in match_logs] == ["score_changed"]
    assert match_logs[0]["message"] == "比分变化 0-0 -> 1-1"


@pytest.mark.asyncio
async def test_discriminator_logs_first_known_score_when_previous_score_is_missing():
    store = MemoryStore()
    listener = Listener(store=store, broadcaster=BroadcastHub())

    await listener._discriminator.process_external_state(
        source="asa_live",
        guid="guid-match-1",
        payload={},
        previous={},
        current={
            "score_home": 2,
            "score_away": 0,
            "updated_at_utc": "2026-05-02T14:30:00Z",
        },
        mapping={"score_home": "score_home", "score_away": "score_away"},
        ws_message={"type": "match.update", "match": {"guid": "guid-match-1"}},
    )

    match_logs = await store.stream("stream:match_logs")

    assert [row["event_kind"] for row in match_logs] == ["score_changed"]
    assert match_logs[0]["message"] == "比分 2-0"


@pytest.mark.asyncio
async def test_discriminator_logs_pm_start_and_finish_status_but_not_break_status():
    store = MemoryStore()
    listener = Listener(store=store, broadcaster=BroadcastHub())

    for previous, current in [
        ({"status": "scheduled"}, {"status": "live", "updated_at_utc": "2026-05-02T14:00:00Z"}),
        ({"status": "live"}, {"status": "Break", "updated_at_utc": "2026-05-02T14:45:00Z"}),
        ({"status": "live"}, {"status": "finished", "updated_at_utc": "2026-05-02T15:50:00Z"}),
    ]:
        await listener._discriminator.process_external_state(
            source="pm_sports",
            guid="guid-match-1",
            payload={},
            previous=previous,
            current=current,
            mapping={"status": "status"},
            ws_message={"type": "match.update", "match": {"guid": "guid-match-1"}},
        )

    match_logs = await store.stream("stream:match_logs")

    assert [row["message"] for row in match_logs] == ["比赛状态 live", "比赛状态 finished"]


@pytest.mark.asyncio
async def test_listener_sends_unknown_guid_payloads_to_dead_letter_only():
    store, _guid = await seed_store()
    hub = BroadcastHub()
    trader = TraderManager(store=store)
    listener = Listener(store=store, broadcaster=hub, trader_manager=trader)

    event = await listener.process_payload(
        "pm_market",
        {"asset_id": "missing-asset", "bid": 0.1, "ask": 0.2, "message_id": "bad-1"},
    )

    assert event is None
    assert await hub.drain() == []
    dead_letters = await store.stream("stream:dead_letters")
    assert dead_letters[-1]["source"] == "pm_market"
    assert dead_letters[-1]["reason"] == "unknown_guid"


@pytest.mark.asyncio
async def test_listener_routes_unchanged_market_tick_without_standard_event():
    store, guid = await seed_store()
    hub = BroadcastHub()
    trader = TraderManager(store=store)
    created = await trader.create_trading(
        {
            "strategy_name": "football_score_delay_trade",
            "strategy_params": {"initial_balance": 1000},
            "affect_sports": ["football"],
            "mode": "simulation",
        }
    )
    await trader.start_trading(created.trading_id)
    pm = await store.get_json(f"pm:match:{guid}")
    await store.set_json(f"pm:match:{guid}", {**pm, "status": "live"})
    pm = await store.get_json(f"pm:match:{guid}")
    asset_id = "asset-ars-home"

    event = await Listener(store=store, broadcaster=hub, trader_manager=trader).process_payload(
        "pm_market",
        {
            "asset_id": asset_id,
            "bid": pm["home_bid1"],
            "ask": pm["home_ask1"],
            "ts": "2026-05-01T19:01:00Z",
        },
    )

    assert event is not None
    assert event["event_type"] == "market_tick"
    assert trader.queue_size(created.trading_id) == 0
    assert await store.stream("stream:standard_events") == []
    assert (await hub.drain())[-1]["topic"] == "market.tick"


@pytest.mark.asyncio
async def test_listener_reconciles_pm_user_account_without_trader_queue_side_effects():
    store, guid = await seed_store()
    hub = BroadcastHub()
    trader = RecordingTraderManager()
    listener = Listener(store=store, broadcaster=hub, trader_manager=trader)

    await listener.process_payload(
        "pm_sports",
        {
            "event_id": "pm-ars-che",
            "status": "live",
            "score": {"home": 0, "away": 0},
            "match_time": "1H 05:00",
            "message_id": "sports-1",
        },
    )
    events_before_user = list(trader.events)
    standard_events_before_user = await store.stream("stream:standard_events")
    event = await listener.process_payload(
        "pm_user",
        {
            "account_alias": "pm-main",
            "balance": 1000,
            "available_cash": 950,
            "message_id": "user-1",
        },
    )

    account = await store.get_json("account:pm-main")
    provider_account = await store.get_json("account:pm:pm-main")
    account_events = await store.stream("stream:account_events:pm:pm-main")

    assert event is not None
    assert event["source"] == "pm_user"
    assert event["provider"] == "pm"
    assert event["account_alias"] == "pm-main"
    assert account["available_cash"] == 950
    assert provider_account["available_cash"] == 950
    assert trader.events == events_before_user
    assert trader.account_events == [event]
    assert account_events[-1]["account"]["available_cash"] == 950
    assert await store.stream("stream:standard_events") == standard_events_before_user
    assert (await store.get_json(f"pm:match:{guid}"))["status"] == "live"


@pytest.mark.asyncio
async def test_listener_reconnects_source_after_failure_and_processes_payload():
    store, guid = await seed_store()
    source = FlakySource(
        {
            "event_id": "pm-ars-che",
            "status": "live",
            "score": {"home": 1, "away": 0},
            "match_time": "1H 12:00",
            "message_id": "sports-reconnect-1",
        }
    )
    listener = Listener(
        store=store,
        broadcaster=BroadcastHub(),
        trader_manager=TraderManager(store=store),
        sources=[source],
        reconnect_delay_seconds=0.01,
    )

    await listener.start()
    try:
        await _wait_until(lambda: source.attempts >= 2 and source.delivered)
    finally:
        await listener.stop()

    pm_match = await store.get_json(f"pm:match:{guid}")
    status = listener.status()
    assert pm_match["score_home"] == 1
    assert source.attempts >= 2
    assert status["external_stream_started"] is False
    assert status["polymarket_last_error"] is None


@pytest.mark.asyncio
async def test_listener_does_not_report_connected_before_source_confirms_transport():
    store, _guid = await seed_store()
    source = SlowConnectSource()
    listener = Listener(
        store=store,
        broadcaster=BroadcastHub(),
        trader_manager=TraderManager(store=store),
        sources=[source],
        reconnect_delay_seconds=0.01,
    )

    await listener.start()
    try:
        await _wait_until(lambda: source.attempts >= 1)
        status = listener.status()
    finally:
        await listener.stop()

    assert status["pm_sports_ws_connected"] is False
    assert status["polymarket_ws_connected"] is False


@pytest.mark.asyncio
async def test_listener_marks_source_disconnected_when_transport_reports_disconnect():
    store, _guid = await seed_store()
    source = StatusSource(
        "pm_user",
        [
            {"__connection_status__": "connected"},
            {"__connection_status__": "disconnected"},
        ],
    )
    listener = Listener(
        store=store,
        broadcaster=BroadcastHub(),
        trader_manager=TraderManager(store=store),
        sources=[source],
        reconnect_delay_seconds=0.01,
    )

    await listener.start()
    try:
        await _wait_until(lambda: source.delivered >= 2)
        status = listener.status()
    finally:
        await listener.stop()

    assert status["pm_user_ws_connected"] is False
    assert status["polymarket_ws_connected"] is False


class FlakySource:
    name = "pm_sports"

    def __init__(self, payload):
        self.payload = payload
        self.attempts = 0
        self.delivered = False

    async def listen(self):
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("first connection failed")
        self.delivered = True
        yield self.payload
        while True:
            await asyncio.sleep(1)


class SlowConnectSource:
    name = "pm_sports"

    def __init__(self):
        self.attempts = 0

    async def listen(self):
        self.attempts += 1
        await asyncio.sleep(1)
        if False:
            yield {}


class StatusSource:
    def __init__(self, name, payloads):
        self.name = name
        self.payloads = payloads
        self.delivered = 0

    async def listen(self):
        for payload in self.payloads:
            self.delivered += 1
            yield payload
        while True:
            await asyncio.sleep(1)


async def _wait_until(predicate, timeout_seconds: float = 0.5) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")
