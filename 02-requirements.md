# PDT2.1 需求分析

## 1. 范围

第一版只做：

- base：PM。
- outsource：GS。
- sport：football。
- storage：Redis。
- backend：Python asyncio。
- frontend：复制原前端页面，页面结构和交互先不改。

明确不做：

- 不接 KS。
- 不接 TRD。
- 不做篮球。
- 不接 PostgreSQL/Timescale。
- 不做复杂多源框架。

## 2. 主流程

系统流程固定为：

1. Collector 调 PM 和 GS HTTP 接口采集足球比赛。
2. Collector 以 PM 比赛为基准，调用 GS `home` 和 `d1` 数据匹配对应比赛。
3. 匹配成功后生成全局唯一 `guid`。
4. 匹配后的 PM 比赛及 PM/GS 对应关系存为一条绑定记录，TTL 5 天。
5. Listener 开启 PM sports WS、PM market WS、PM user WS、GS live WS。
6. Listener 收到数据后：
   - 只保留系统当前存在的比赛，即能解析到 `guid` 的比赛。
   - 保存 source raw payload。
   - 标准化时间和字段。
   - 检测比分、时间、盘口、事件变化。
   - 分别写入 PM 数据、GS 数据、orderbook、日志、账户状态。
   - 推送给当前正在运行的交易员和前端。
7. Trader 分为模拟交易员和真实交易员。
8. 每个 Trader 绑定一个策略和一个账户。
9. 策略判断收到的实时数据怎么用。
10. Trader 通用模块执行模拟交易或真实交易。

## 3. 数据原则

- `guid` 是系统内唯一比赛 ID。
- PM 数据和 GS 数据逻辑独立、存储独立，互不覆盖。
- PM 是基准源，GS 只通过 `guid` 关联。
- 源数据是什么就存什么。
- raw payload 不改、不美化、不为了前端补字段。
- 标准化字段只为内部处理服务，不能覆盖 raw payload。
- 前端展示如果字段缺失，就展示缺失或空状态，不编造数据。

## 4. PM 数据需求

Collector 需要拿到：

- PM event id。
- slug。
- sport。
- league。
- start time。
- home/away。
- status。
- score。
- moneyline markets。
- condition id。
- token id / asset id。
- best bid/ask 或 outcome price。
- total volume。
- moneyline volume。
- raw payload。

Listener 需要处理：

- sports WS：比分、比赛状态、比赛时间。
- market WS：moneyline orderbook、ask1、bid1、price changes。
- user WS：真实账户余额、订单、成交、持仓相关变化。

## 5. GS 数据需求

Collector 需要调用：

- GS `home`。
- GS `d1`。

Collector 需要拿到：

- GS match id。
- pregame id。
- inplay id。
- league。
- start time。
- home/away。
- status。
- score。
- stat/clock。
- odds。
- raw payload。

Listener 需要处理：

- live score。
- live status。
- live clock/stat。
- 红黄牌。
- 换人。
- VAR。
- 点球。
- 任意球。
- live odds，如果有。

## 6. 匹配需求

匹配必须以 PM 为主。

绑定记录必须包含：

- `guid`。
- PM event id。
- PM slug。
- PM market/condition/token id。
- GS match id。
- GS pregame id。
- GS inplay id。
- confidence。
- binding status。
- created_at / updated_at。

匹配优先级：

1. 已有人工绑定。
2. PM game id 与 GS 相关 id 可直接对应时使用直接匹配。
3. GS pregame/inplay mapping。
4. 队名归一化相似度 + 开赛时间窗口。
5. 不确定就 pending，不强行匹配。

绑定记录 TTL：5 天。

## 7. Listener 标准化字段

Listener 标准化后推送的数据必须至少包含：

- `received_at_utc`：系统接收时间。
- `pushed_at_utc`：推送给 trader/frontend 的时间。
- `source`：pm_sports / pm_market / pm_user / gs_live。
- `guid`。
- `score_home` / `score_away`。
- `match_time`：比赛进行时间原始/标准化展示值。
- `period`。
- `clock`。
- `red_cards`。
- `yellow_cards`。
- `substitutions`。
- `var_events`。
- `penalties`。
- `free_kicks`。
- `moneyline`：home/draw/away ask1/bid1。
- `changed_fields`。
- `raw_ref`。

无法解析 `guid` 的 WS 数据进入 dead-letter，不推送给交易员。

## 8. Trader 需求

Trader 是交易通用模块，不是策略模块。

Trader 需要提供给策略调用的 API：

- 写交易日志。
- 写运行日志。
- 买入。
- 卖出。
- 查询当前盘口。
- 查询资产。
- 查询持仓。
- 查询余额。
- 查询当前比赛 PM 数据。
- 查询当前比赛 GS 数据。

这些 API 只能通过 Trader 暴露，策略不能直接访问 Redis、PM gateway 或账户密钥。

## 9. Trader 参数

每个 Trader 关联：

- 一个 mode：simulation 或 real。
- 一个 strategy。
- 一个账户：模拟账户或 PM real account alias。

通用参数：

- `max_positions`：最大持仓数。
- `max_fund_usage_pct`：资金利用率上限。
- `max_single_order_pct`：单笔上限，占账户权益百分比。
- `max_add_count`：最多加仓次数。
- `max_add_fund_pct`：加仓资金上限，占账户权益百分比。
- `stop_loss_drawdown`：自动止损回撤值，默认 0.05。

Trader 执行指令前必须验证：

- 可用额度。
- 资金使用上限。
- 单笔上限。
- 最大持仓数。
- 加仓次数。
- 加仓资金上限。
- buy 必须有 ask1。
- sell 必须有 bid1 和可卖持仓。
- real mode 必须通过真实交易开关和账户校验。

## 10. 默认策略：比分时差交易

首版默认策略为 `football_score_delay_trade`。

触发逻辑：

- 当 GS 比分更新早于 PM 时触发。
- 例如 GS 为 1-0 而 PM 仍为 0-0，则买入 GS 领先方向对应的 PM moneyline。

反手逻辑：

- 若新信号与当前持仓方向不一致，立即反手：
  - 先平掉当前方向。
  - 再买入新方向。

附加约束：

- 85 分钟后不建仓，只允许平仓。
- ask1 > 0.9 不建仓。
- ask1 > 0.95 不加仓。
- 持仓方向 ask1 > 0.95 且其他方向比分变化时立即平仓。

策略只返回交易意图，不直接下单。

## 11. 执行需求

simulation：

- buy 用 ask1 成交。
- sell 用 bid1 成交。
- 更新模拟账户、持仓、成交、收益。

real：

- 只走 PM。
- 凭证只从 `.env` 读取。
- 下单前必须经过 Trader 通用约束校验。
- 下单结果通过 PM user WS 或查询回填。
- 默认 dry-run，不自动真实下单。

