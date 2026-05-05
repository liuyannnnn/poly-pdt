# Redis 数据设计

Redis 是 PDT2.1 第一版唯一运行存储。设计目标不是做复杂数据仓库，而是把比赛当前态、行情时序、外部数据源、交易员账本和运行日志分开，并保证 PM、GS、ASA 等数据源互不覆盖、互不污染。

## 1. 总原则

- `guid` 是系统内比赛唯一 ID。
- PM、GS、ASA 分开存：PM 只写 `pm:*`，GS 只写 `gs:*`，ASA 只写 `asa:*`。
- 多数据源只通过 `binding:{guid}` 和 `idx:*` 建立关系。
- 展示 API 可以临时聚合 PM/GS/ASA，但 Redis 原始当前态不混写。
- 行情 ticks、ALL 采集点、LIVE 10 秒点、比赛日志、交易记录都按追加方式写入。
- 已写入的历史数据不回读重写、不过滤、不补点、不拟合；存的是什么就显示什么。
- PM market ticks 只属于行情链路，不进入判别器。
- PM sports、GS、ASA 等比赛数据进入判别器，形成比赛变化信号。
- 交易模块维护最新行情内存态，供策略和交易员快速查询；Redis 保存当前态和可追溯的追加数据。
- 交易员、账户、订单、成交、日志和比赛数据分开。
- 实盘账号按 `provider + account_alias` 隔离。当前 provider 是 PM，后续可以增加 KS。
- 密钥、token、签名、密码不进 Redis；写 Redis 前统一脱敏。

## 2. 简版结构

| 模块 | Key | 数据说明 | 关键字段 |
| --- | --- | --- | --- |
| PM 比赛 | `pm:match:{guid}` | 每场 PM 比赛一条当前态，Collector/PM sports/PM market 更新 | `guid`, `pm_event_id`, `slug`, `league`, `home_team`, `away_team`, `start_time_utc`, `status`, `score_home`, `score_away`, `total_volume`, `moneyline_volume`, `home/draw/away_asset_id`, `home/draw/away_ask1`, `home/draw/away_bid1`, `updated_at_utc` |
| GS 比赛 | `gs:match:{guid}` | 每场 GS 比赛一条当前态，GS HTTP/WS 更新 | `guid`, `gs_match_id`, `gs_pregame_id`, `gs_inplay_id`, `league`, `home_team`, `away_team`, `start_time_utc`, `status`, `score_home`, `score_away`, `match_time`, `period`, `clock`, `red_cards`, `yellow_cards`, `corners`, `shots_on_target`, `events`, `lineups`, `updated_at_utc` |
| ASA 比赛 | `asa:match:{guid}` | 每场 ASA 比赛一条当前态，ASA HTTP/WS 更新 | `guid`, `asa_event_key`, `league`, `home_team`, `away_team`, `start_time_utc`, `status`, `score_home`, `score_away`, `match_time`, `period`, `red_cards`, `yellow_cards`, `corners`, `shots_on_target`, `penalties`, `updated_at_utc` |
| 数据源绑定 | `binding:{guid}` | PM 基准比赛与 GS/ASA 比赛的对应关系 | `guid`, `pm_event_id`, `pm_slug`, `pm_condition_id`, `pm_*_asset_id`, `gs_match_id`, `gs_inplay_id`, `asa_event_key`, `confidence`, `status`, `created_at_utc`, `updated_at_utc` |
| PM 当前盘口 | `orderbook:{guid}:{outcome}` | PM moneyline 高频盘口当前态 | `guid`, `outcome_key`, `asset_id`, `ask1`, `bid1`, `updated_at_utc`, `source` |
| ALL 采集曲线 | `series:pm:collector:{guid}` | Collector 每次 HTTP 采集到的 moneyline ask1/bid1 追加点 | `guid`, `sample_ts_utc`, `home/draw/away_ask1`, `home/draw/away_bid1`, `total_volume`, `moneyline_volume` |
| LIVE 原始 ticks | `series:pm:ticks:{guid}` | PM market WS 比赛中收到的原始行情 ticks 追加点 | `guid`, `received_at_utc`, `asset_id`, `outcome_key`, `ask1`, `bid1`, `raw_ref` |
| LIVE 10 秒曲线 | `series:pm:10s:{guid}` | 后台每 10 秒从 ticks 中按真实收到的数据重采样出的展示点 | `guid`, `sample_ts_utc`, `home/draw/away_ask1`, `home/draw/away_bid1` |
| 比赛变化事件 | `stream:standard_events` | 判别器输出的比赛信号，给交易模块和审计/恢复用 | `source`, `guid`, `event_type`, `old_value`, `new_value`, `pm_score_home_at_event`, `pm_score_away_at_event`, `received_at_utc` |
| 比赛/策略日志 | `stream:match_logs` | 只追加赛况关键变化、交易员买卖/不交易理由、系统异常 | `source`, `guid`, `trader_id`, `level`, `message`, `ts_utc` |
| 未识别消息 | `stream:dead_letters` | 无法解析 `guid` 或不支持 source 的消息 | `source`, `reason`, `payload`, `ts_utc` |
| 订单/成交流 | `stream:orders`, `stream:fills` | PM user WS、后续 KS user WS 或 dry-run 产生的订单、成交记录 | `provider`, `account_alias`, `trader_id`, `order_id`, `fill_id`, `guid`, `asset_id`, `price`, `size`, `status`, `ts_utc` |
| 外部账户 | `account:{provider}:{alias}` | PM user WS/PM API 回填的账户快照；后续 KS 使用同样模式 | `provider`, `account_alias`, `balance`, `available_cash`, `positions`, `updated_at_utc` |
| 账号事件 | `stream:account_events:{provider}:{alias}` | user WS 标准化后的账号、订单、成交事件，直接路由给对应交易员 | `provider`, `account_alias`, `event_type`, `order_id`, `fill_id`, `payload_ref`, `ts_utc` |
| Trader 配置/状态 | `trader:{trader_id}:config`, `trader:{trader_id}:state` | 交易实例配置和运行状态 | `trader_id`, `mode`, `provider`, `account_alias`, `strategy_name`, `strategy_params`, `common_params`, `status` |
| Trader 账户/持仓/交易/日志 | `trader:{trader_id}:account`, `trader:{trader_id}:positions`, `trader:{trader_id}:trades`, `trader:{trader_id}:logs` | simulation/dry-run 交易内部账本；实盘展示以 provider 返回为准，本地只做必要缓存 | `provider`, `account_alias`, `available_cash`, `positions`, `trades`, `logs`, `realized_pnl`, `unrealized_pnl` |

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

idx:asa:event:{asa_event_key} -> guid
idx:asa:inplay:{asa_event_key} -> guid

idx:guid:{guid} -> guid
idx:match:status:{status}:{guid} -> guid
idx:account:{provider}:{account_id_or_address} -> account_alias
idx:account:alias:{provider}:{alias} -> account:{provider}:{alias}
idx:traders -> latest trader id
```

说明：

- PM sports WS 可以用 `event_id`、`slug`、`game_id` 找 `guid`。
- PM market WS 用 `asset_id` 找 `guid|outcome_key`，只更新对应 outcome 的盘口。
- GS live WS 用 `inplay_id` 或 `match_id` 找 `guid`。
- ASA WS 用 `event_key` 找 `guid`。
- 账号索引用于 PM user WS、后续 KS user WS 把账户事件路由到对应 `account_alias`。
- `idx:traders` 当前只是辅助信息，API 主要通过 `trader:*:account` 等 key 扫描持久化 trader 状态。

## 4. 生命周期

比赛相关数据保留 3 天：

- `pm:match:{guid}`
- `gs:match:{guid}`
- `asa:match:{guid}`
- `binding:{guid}`
- `idx:pm:*`
- `idx:gs:*`
- `idx:asa:*`
- `idx:guid:*`
- `idx:match:*`
- `series:pm:collector:{guid}`
- `series:pm:10s:{guid}`
- `stream:standard_events`
- `stream:match_logs`
- `stream:dead_letters`

LIVE 原始 ticks 保留 24 小时：

- `series:pm:ticks:{guid}`

短 TTL 当前态：

- `orderbook:{guid}:{outcome}`：10 分钟。
- `pm:raw:*`、`gs:raw:*`、`asa:raw:*`：10 分钟。

长期保留，除非用户删除交易员或账户：

- `trader:{trader_id}:config`
- `trader:{trader_id}:state`
- `trader:{trader_id}:account`
- `trader:{trader_id}:positions`
- `trader:{trader_id}:trades`
- `trader:{trader_id}:logs`
- `account:{provider}:{alias}`
- `stream:orders`
- `stream:fills`
- `stream:account_events:{provider}:{alias}`

删除交易员时，必须同时删除该交易员的配置、状态、账户、持仓、交易、日志。本地实盘缓存也随交易员删除而删除；PM 真实账户和 PM 真实订单不由 Redis 删除。

## 5. 多数据源隔离规则

PM 数据源只允许写：

```text
pm:match:{guid}
idx:pm:*
orderbook:{guid}:{outcome}
series:pm:collector:{guid}
series:pm:ticks:{guid}
series:pm:10s:{guid}
stream:orders
stream:fills
account:{provider}:{alias}
stream:account_events:{provider}:{alias}
stream:standard_events
stream:match_logs
stream:dead_letters
```

后续 KS user listener 只允许写：

```text
account:ks:{alias}
stream:account_events:ks:{alias}
stream:orders
stream:fills
stream:dead_letters
```

GS 数据源只允许写：

```text
gs:match:{guid}
idx:gs:*
stream:standard_events
stream:match_logs
stream:dead_letters
```

ASA 数据源只允许写：

```text
asa:match:{guid}
idx:asa:*
stream:standard_events
stream:match_logs
stream:dead_letters
```

Collector 可以写：

```text
pm:match:{guid}
gs:match:{guid}
asa:match:{guid}
binding:{guid}
idx:pm:*
idx:gs:*
idx:asa:*
idx:guid:{guid}
idx:match:status:{status}:{guid}
series:pm:collector:{guid}
collector:last_report
```

Trader 只允许写：

```text
trader:{trader_id}:*
stream:orders
stream:fills
stream:match_logs
```

交易模块可以读取 PM/GS/ASA 当前态、最新行情内存态和 provider 账号缓存，但策略不能直接写 Redis、不能直接调用 PM/KS gateway。

## 6. 交易模块内存态

交易模块维护一个进程内 `MarketState`，保存每场比赛当前可用的最新行情：

```text
guid
home/draw/away asset_id
home/draw/away ask1
home/draw/away bid1
updated_at_utc
source
```

使用规则：

- Listener 收到 PM market tick 后，先标准化并更新 `MarketState`。
- 然后异步追加 Redis ticks/当前盘口，并异步推送前端和交易员。
- 交易员或策略查询盘口时，只通过交易模块 API 查询 `MarketState`。
- 买入/卖出前，交易模块再查一次 PM CLOB。
- 买入只比较 ask1，使用 WS/内存 ask1 与 CLOB ask1 中更高的价格。
- 卖出只比较 bid1，使用 WS/内存 bid1 与 CLOB bid1 中更低的价格。
- 只有两者差值绝对值 `> 0.01` 时写提示日志，等于 `0.01` 不算超过。
- `MarketState` 不是历史存储；进程重启后从 Redis 当前态或最新 ticks 恢复。

## 7. 追加写入规则

以下 key 必须按追加方式写入：

```text
series:pm:collector:{guid}
series:pm:ticks:{guid}
series:pm:10s:{guid}
stream:standard_events
stream:match_logs
stream:orders
stream:fills
trader:{trader_id}:trades
trader:{trader_id}:logs
```

约束：

- 不为了修正历史展示，把旧记录取出来再修改后写回。
- 不删除或过滤已写入的历史日志。
- 新格式只影响新写入记录。
- 图表直接显示 Redis 中真实存在的点。
- ALL 图表只读 `series:pm:collector:{guid}`。
- LIVE 图表只读比赛开始后的 `series:pm:10s:{guid}`。
- 未开始比赛不展示 LIVE 图表，也不保存 LIVE ticks。

## 8. 不做的复杂化

- 不把 PM、GS、ASA 合并成一个 `match:{guid}` 主表。
- 不在 Redis 里做多市场通用框架。
- 不实现 KS/TRD/篮球业务表。只保留最小 `provider` 字段和 gateway 形态，避免后续 PM/KS 多账户接入时重写交易员和策略。
- 不在 Redis 里做策略计算。
- 不在 Redis 里做 ticks 去重、补点或拟合。
- 不把凭证、token、签名、密码放进 Redis。

## 9. 展示层聚合

前端需要的一场比赛可以由 API 临时聚合：

```text
pm:match:{guid}
+ gs:match:{guid}
+ asa:match:{guid}
+ binding:{guid}
+ orderbook:{guid}:home/draw/away
+ series:pm:collector:{guid}
+ series:pm:10s:{guid}
```

聚合只发生在 API 返回时，不反写成一个混合 Redis 记录。这样 PM 更新不会覆盖 GS/ASA 比分，GS/ASA 更新也不会覆盖 PM 盘口。
