# Redis 数据设计

Redis 是 PDT2.1 第一版唯一运行存储。设计目标不是做复杂数据仓库，而是把几个运行功能分开，并保证 PM 和 GS 两个数据源互不覆盖、互不污染。

## 1. 总原则

- `guid` 是系统内比赛唯一 ID。
- PM 和 GS 分开存：PM 只写 `pm:*`，GS 只写 `gs:*`。
- 两个数据源只通过 `binding:{guid}` 和 `idx:*` 建立关系。
- 展示 API 可以聚合 PM/GS，但 Redis 原始当前态不混写。
- orderbook 高频数据独立短 TTL。
- 交易员、账户、订单、成交、日志和比赛数据分开。
- 密钥、token、签名、密码不进 Redis；写 Redis 前统一脱敏。

## 2. 简版结构

| 模块 | Key | 数据说明 | 关键字段 |
| --- | --- | --- | --- |
| PM 比赛 | `pm:match:{guid}` | 每场 PM 比赛一条当前态，Collector/PM WS 更新 | `guid`, `pm_event_id`, `slug`, `league`, `home_team`, `away_team`, `start_time_utc`, `status`, `score_home`, `score_away`, `total_volume`, `moneyline_volume`, `home/draw/away_asset_id`, `home/draw/away_ask1`, `home/draw/away_bid1`, `updated_at_utc` |
| GS 比赛 | `gs:match:{guid}` | 每场 GS 比赛一条当前态，GS HTTP/WS 更新 | `guid`, `gs_match_id`, `gs_pregame_id`, `gs_inplay_id`, `league`, `home_team`, `away_team`, `start_time_utc`, `status`, `score_home`, `score_away`, `match_time`, `period`, `clock`, `odds_*`, `red_cards`, `yellow_cards`, `corners`, `shots_on_target`, `events`, `lineups`, `updated_at_utc` |
| PM/GS 绑定 | `binding:{guid}` | PM 基准比赛与 GS 比赛的对应关系 | `guid`, `pm_event_id`, `pm_slug`, `pm_condition_id`, `pm_*_asset_id`, `gs_match_id`, `gs_pregame_id`, `gs_inplay_id`, `confidence`, `status`, `created_at_utc`, `updated_at_utc` |
| Orderbook | `orderbook:{guid}:{outcome}` | PM moneyline 高频盘口当前态，多条/每场 | `guid`, `outcome_key`, `asset_id`, `ask1`, `bid1`, `updated_at_utc`, `source` |
| 标准事件 | `stream:standard_events` | Listener 标准化后的变化事件，给审计/恢复用 | `source`, `guid`, `changed_fields`, `pm_ref`, `gs_ref`, `orderbook_ref`, `received_at_utc` |
| 未识别消息 | `stream:dead_letters` | 无法解析 `guid` 或不支持 source 的消息 | `source`, `reason`, `payload`, `ts_utc` |
| 订单/成交流 | `stream:orders`, `stream:fills` | PM user WS 或 dry-run 产生的订单、成交记录 | `order_id`, `fill_id`, `guid`, `asset_id`, `price`, `size`, `status`, `ts_utc` |
| 外部账户 | `account:{alias}` | PM user WS 回填的账户快照 | `account_alias`, `balance`, `available_cash`, `positions`, `updated_at_utc` |
| Trader 配置/状态 | `trader:{trader_id}:config`, `trader:{trader_id}:state` | 交易实例配置和运行状态 | `trading_id`, `mode`, `strategy_name`, `strategy_params`, `status` |
| Trader 账户/持仓/交易/日志 | `trader:{trader_id}:account`, `trader:{trader_id}:positions`, `trader:{trader_id}:trades`, `trader:{trader_id}:logs` | dry-run/simulation 交易内部账本 | `available_cash`, `positions`, `trades`, `logs`, `unrealized_pnl` |

## 3. 索引

索引只做快速定位，不承载业务状态。

```text
idx:pm:event:{pm_event_id} -> guid
idx:pm:slug:{slug} -> guid
idx:pm:game:{game_id} -> guid
idx:pm:asset:{asset_id} -> guid|outcome_key

idx:gs:id:{gs_match_id} -> guid
idx:gs:pregame:{gs_pregame_id} -> guid
idx:gs:inplay:{gs_inplay_id} -> guid

idx:guid:{guid} -> guid
idx:match:status:{status}:{guid} -> guid
idx:traders -> latest trader id
```

说明：

- PM sports WS 可以用 `event_id`、`slug`、`game_id` 找 `guid`。
- PM market WS 用 `asset_id` 找 `guid|outcome_key`，只更新对应 outcome 的盘口。
- GS live WS 用 `inplay_id` 或 `match_id` 找 `guid`。
- `idx:traders` 当前只是辅助信息，API 主要通过 `trader:*:account` 等 key 扫描持久化 trader 状态。

## 4. 生命周期

5 天 TTL：

- `pm:match:{guid}`
- `gs:match:{guid}`
- `binding:{guid}`
- `idx:pm:*`
- `idx:gs:*`
- `idx:guid:*`
- `idx:match:*`

10 分钟 TTL：

- `orderbook:{guid}:{outcome}`
- `pm:raw:*`
- `gs:raw:*`

长期保留，除非用户删除：

- `trader:{trader_id}:config`
- `trader:{trader_id}:state`
- `trader:{trader_id}:account`
- `trader:{trader_id}:positions`
- `trader:{trader_id}:trades`
- `trader:{trader_id}:logs`
- `account:{alias}`
- `stream:*`

## 5. 多数据源隔离规则

PM 数据源只允许写：

```text
pm:match:{guid}
idx:pm:*
orderbook:{guid}:{outcome}
stream:orders
stream:fills
account:{alias}
stream:standard_events
stream:dead_letters
```

GS 数据源只允许写：

```text
gs:match:{guid}
idx:gs:*
stream:standard_events
stream:dead_letters
```

Collector 可以写：

```text
pm:match:{guid}
gs:match:{guid}
binding:{guid}
idx:pm:*
idx:gs:*
idx:guid:{guid}
idx:match:status:{status}:{guid}
collector:last_report
```

Trader 只允许写：

```text
trader:{trader_id}:*
stream:standard_events
```

## 6. 不做的复杂化

- 不把 PM 和 GS 合并成一个 `match:{guid}` 主表。
- 不在 Redis 里做多市场通用框架。
- 不为 KS/TRD/篮球预留 key。
- 不把历史行情完整落 Redis；高频 orderbook 只保留当前态和短期 raw。
- 不把凭证、token、签名、密码放进 Redis。

## 7. 展示层聚合

前端需要的一场比赛可以由 API 临时聚合：

```text
pm:match:{guid}
+ gs:match:{guid}
+ binding:{guid}
+ orderbook:{guid}:home/draw/away
```

聚合只发生在 API 返回时，不反写成一个混合 Redis 记录。这样 PM 更新不会覆盖 GS 比分，GS 更新也不会覆盖 PM 盘口。
