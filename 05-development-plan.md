# PDT2.1 三轮开发计划

## 目标

用最少可维护代码实现 PM + GS/ASA + 足球 + Redis + 异步交易主链路。

## Agent 分工

本项目只使用 4 类 Agent。

1. Project Manager Agent：控制目标、范围、进度、UI 一致性、代码层级和逻辑简洁性。
2. Collector Agent：只做 PM/GS/ASA HTTP 采集和 PM 主导匹配。
3. Listener Agent：只做 PM/GS/ASA WS 监听、过滤、标准化、入库、推送。
4. Trader Agent：只做交易模块、交易员、策略 API、约束检查、模拟/真实交易、默认策略。

不要再拆 Source/Matcher/Normalizer/API/QA 等额外常驻角色。需要测试和前端兼容时，由 Project Manager 分配到对应 Agent 的范围内完成。

判别器不是新的 Agent。判别器是 Listener 和 Trader 之间的轻量组件，只处理比赛数据变化，不处理 PM market ticks。

## 第一轮：骨架和最小可运行链路

目标：

- FastAPI 能启动。
- Redis 能连接。
- 前端页面从 `pdt2.1/frontend-snapshot/` 复制进新项目 `front/`。
- 前端能拿到空数据。
- Runtime 能启动和关闭。
- WebSocket 能发 heartbeat。
- 目录结构符合简洁原则。

任务：

- Project Manager 建项目骨架。
- 复制 `pdt2.1/frontend-snapshot/` 到新项目 `front/`，不重做 UI。
- 建 `backend/app`。
- 写 `config.py`。
- 写 `store.py`，只封装 Redis 常用操作。
- 写 `api.py`，先返回空 matches/accounts/positions/trades/logs/tradings。
- 写 `/health`。
- 写最小 `runtime.py`。
- 写最小 `/api/v1/ws/market` heartbeat。
- 写空 Collector/Listener/Trader class，先不接外部服务。

验收：

- `pytest` 通过。
- `uvicorn app.main:app` 能启动。
- 前端能打开，且页面结构与快照一致。
- `/api/v1/health` 可用。
- `/api/v1/matches` 可用。
- `/api/v1/tradings` 可用。
- `/api/v1/ws/market` 可用。

## 第二轮：各模块开发

第二轮允许多 Agent 并行，但必须按职责隔离。

### 2.1 Collector Agent

目标：

- 调 PM HTTP 采集足球比赛。
- 调 GS `home` 和 `d1` 采集足球比赛。
- 调 ASA HTTP 采集或查询近期足球比赛。
- 以 PM 为基准匹配 GS/ASA。
- 生成 `guid`。
- 保存 PM 当前态、GS/ASA 当前态、binding。
- TTL 3 天。

任务：

- 实现 PM HTTP client/parser/collector。
- 实现 GS HTTP client/parser/collector。
- 实现 ASA HTTP client/parser/collector。
- 实现队名归一化。
- 实现 PM 主导匹配。
- 写 `pm:match:{guid}`。
- 写 `gs:match:{guid}`。
- 写 `asa:match:{guid}`。
- 写 `binding:{guid}`。
- 写 PM/GS/ASA/guid 索引。
- 写 collector run report。

验收：

- PM collector fixture test。
- GS collector fixture test。
- ASA collector fixture test。
- matcher test：直接匹配、队名+时间、pending。
- TTL 3 天测试。
- PM 和 GS/ASA 数据不互相覆盖。

### 2.2 Listener Agent

目标：

- 接 PM sports WS。
- 接 PM market WS。
- 接 PM user WS。
- 接 GS live WS。
- 接 ASA live WS。
- 只保留系统当前存在的 `guid`。
- 标准化实时字段。
- 入 Redis。
- 推送给前端。
- PM market ticks 进入交易模块行情通道。
- PM sports/GS/ASA 比赛数据进入判别器。
- PM user 账号、订单、成交事件进入账号事件通道，按账号路由给对应交易员。

任务：

- 实现 PM sports parser/listener。
- 实现 PM market parser/listener，更新 moneyline ask1/bid1。
- 实现 PM user parser/listener，更新用户账户、订单、成交并推送前端和对应交易员。
- 实现 GS live parser/listener。
- 实现 ASA live parser/listener。
- 实现 guid resolver。
- 实现标准化字段：
  - received_at_utc。
  - pushed_at_utc。
  - source。
  - guid。
  - 比分。
  - 进行时间。
  - 红黄牌。
  - 换人。
  - VAR。
  - 点球。
  - 任意球。
  - moneyline ask1/bid1。
- PM market tick 标准化后立即：
  - 更新交易模块最新行情内存态。
  - 追加写入 ticks。
  - 推送前端。
  - 推送 Trader market tick 通道。
- PM sports/GS/ASA 比赛数据标准化后立即：
  - 写各自数据源当前态。
  - 推送前端。
  - 推送判别器。
- PM user 账号事件标准化后立即：
  - 解析 `provider + account_alias`。
  - 写账号、订单、成交缓存或追加记录。
  - 推送前端。
  - 推送绑定该账号的 Trader account event 通道。
- unknown guid 进入 dead-letter。

验收：

- parser fixture test。
- unknown guid 不推送交易员。
- PM 字段不被 GS/ASA 覆盖。
- GS/ASA 字段不被 PM 覆盖。
- Market WS 能更新 home/draw/away ask1/bid1。
- User WS 能更新账户状态并推送前端。
- PM market tick 不进入判别器。
- PM user 账号事件不进入判别器，不进入策略。
- 比赛未开始时不保存 LIVE ticks，不展示 LIVE 图表。

### 2.2.1 判别器组件

目标：

- 只处理比赛数据。
- 只做简单变化检测。
- 把比赛变化转成交易模块可订阅的 match signal。

任务：

- 在内存中记录每个 `source + guid` 的最近一次比赛值。
- 对比分、红黄牌、点球、角球、射正等字段做新旧值比较。
- 比分变化写 `SCORE_CHANGED`。
- 点球写 `PENALTIES_CHANGED`。
- PM 比赛开始和结束写状态日志。
- 其他赛况字段只更新当前态和前端展示，不默认写策略日志。
- 生成 match signal 时，记录外部信号到达当刻的 PM base 比分。

验收：

- PM market tick 不会触发判别器。
- ASA/GS 比分变化不会因为同场 PM market tick 高频进入而被丢弃。
- `epl-eve-mac-2026-05-04` 这类 ASA 早于 PM 的事件，交易模块能看到“外部比分”和“当刻 PM 比分”的差异。
- 判别器不读写交易员账户、持仓、订单。

### 2.3 Trader Agent

目标：

- 支持模拟交易员和真实交易员。
- 每个交易员关联一个策略和一个账户。
- 交易模块提供统一 API。
- 交易模块提供实盘 provider 的交易指令和查询指令。
- 交易员执行前做通用约束检查。
- 实现默认策略 `football_score_delay_trade`。

任务：

- 实现 TraderManager。
- 实现交易模块事件入口：
  - `on_market_tick(event)`。
  - `on_match_signal(event)`。
  - `on_account_event(event)`。
- 实现每个 Trader 独立运行状态。
- 实现交易模块最新行情内存态 `MarketState`。
- 实现 Strategy base/registry。
- 实现策略可调用 API：
  - 写交易日志。
  - 写运行日志。
  - 买入。
  - 卖出。
  - 查询当前盘口。
  - 查询资产。
  - 查询持仓。
  - 查询余额。
  - 查询 PM/GS/ASA 当前数据。
- 实现实盘 provider API：
  - 查询 provider 账户。
  - 查询 provider 持仓。
  - 查询 provider 订单。
  - 发出 provider 买入指令。
  - 发出 provider 卖出指令。
- 实现通用参数：
  - 最大持仓数。
  - 资金利用率%。
  - 单笔上限%。
  - 单场最多加仓次数。
  - 单次加仓资金上限%。
  - 自动卖出回撤值，默认 0.05。
- 实现 TradingValidator。
- 实现 SimulationEngine。
- 实现 PM real gateway dry-run 骨架。
- provider 接口保留 PM/KS 两类命名，但当前只实现 PM，不接 KS。
- 实现 `football_score_delay_trade`。
- 买入/卖出前查询 CLOB：
  - 买入只看 ask1，使用 WS/内存 ask1 与 CLOB ask1 中更高的价格。
  - 卖出只看 bid1，使用 WS/内存 bid1 与 CLOB bid1 中更低的价格。
  - 差值绝对值 `> 0.01` 才写提示日志。

默认策略验收：

- GS/ASA 比分早于 PM base 时触发买入或加仓。
- 外部数据源扳平且 PM 还未扳平时，可以买入 Draw。
- 新信号与当前持仓方向不一致时，先平仓，再按策略和通用参数判断是否反手。
- 买入信号方向 ask1 > 0.93 时不建仓、不加仓。
- 85 分钟后，建仓或加仓金额为对应限制金额的一半。
- 领先方净胜球从 2 变 1，且当前持仓方向 bid1 > 0.85 且收益为正时，全部平仓。

通用交易验收：

- 多个 trader 同时运行互不影响。
- 单个 trader 异常不影响其他 trader。
- buy 使用 ask1。
- sell 使用 bid1。
- 买入和卖出数量都是整数份。
- 余额不足拒单。
- 资金使用率超限拒单。
- 最大持仓数超限拒单。
- 加仓次数超限拒单。
- 交易员订阅 market tick 后，按持仓方向 ask1 最高点回落绝对值触发强制平仓。
- 回撤强平是交易员通用逻辑，不是策略逻辑。
- 模拟交易员自行维护账户、持仓、交易记录。
- 实盘交易员通过交易模块调用 provider gateway，账户、持仓、订单、成交以 provider 查询和 user WS 回报为准。
- dry-run 不提交真实订单。
- 密钥不会进入 Redis/log/test fixture。

## 第三轮：集成、联调和测试

目标：

- 把三类模块接成完整链路。
- 前端页面可用。
- Redis 数据结构符合设计。
- 模拟交易能连续运行。
- 真实交易仍默认 dry-run。

任务：

- Project Manager 做集成协调。
- 接通 Collector -> Redis -> API `/matches`。
- 接通 Listener -> Redis -> Frontend WS。
- 接通 Listener PM market ticks -> Trading MarketState -> Trader market tick 通道。
- 接通 Listener 比赛数据 -> 判别器 -> Trader match signal 通道。
- 接通 Listener PM user -> account event router -> Trader account event 通道。
- 接通 Trader -> Redis -> API accounts/positions/trades/logs。
- 接通 PM User WS -> account/order/fill reconciliation。
- 前端 mapper 对齐原页面功能设计。
- Redis TTL/retention cleanup。
- 日志脱敏检查。
- GS HTTP/WS connectivity check。
- PM sports/market/user connectivity check。
- dry-run soak test。

验收：

- collector 连续运行。
- listener 自动重连。
- trader 能稳定接收事件。
- PM market tick、match signal、account event 互相隔离，不能互相覆盖、阻塞或去重丢弃。
- 多个 PM 账户可配置、可监听，并能按 `account_alias` 路由到对应交易员。
- 模拟交易至少跑通一场 fixture 或真实 live replay。
- 前端 matches/detail/accounts/positions/trades/logs 都能读到真实后端数据。
- PM/GS/ASA 通过 guid 唯一关联。
- PM/GS/ASA 存储独立，互不覆盖。
- Redis 中无 PM/GS/ASA 密钥、token、签名原文。
- dry-run 下不会提交真实订单。
- 日志可定位 source、guid、trader_id、order_id。
- 所有测试、lint、前端 build 通过。

## 第四轮：交易模块解耦改造（待确认后执行）

这一轮只在用户确认后改代码。目标是按最新交易架构把“监听器、判别器、交易模块、交易员、策略、通用函数”分清楚，避免高频行情和低频比赛信号互相影响。

### 4.1 架构边界

目标：

- Listener 只做监听、标准化、入库、推送。
- 判别器只做比赛数据变化检测。
- 交易模块只做事件入口、查询、校验、执行和运行态管理。
- 交易员只做账户/持仓/交易记录/统计/通用参数校验/强制平仓。
- 策略只做交易意图判断。
- 实盘 provider gateway 只在交易模块内调用，策略和 Listener 都不能直接调用。

任务：

- PM market WS ticks 单独走行情通道。
- PM sports、GS、ASA 比赛数据单独走比赛数据通道。
- 判别器输出 match signal。
- Trader 接收独立行情和比赛入口：
  - `on_market_tick(event)`。
  - `on_match_signal(event)`。
- Trader 还接收独立账号入口：
  - `on_account_event(event)`。
- 删除按 `guid` 合并或丢弃比赛信号的逻辑。
- 高频 tick 可以在行情通道内部合并内存最新态，但不得影响 match signal。
- 账号事件按 `provider + account_alias` 路由，不按 `guid` 参与行情或比赛信号队列。
- 所有 Redis 历史时序和日志继续追加写入，不重写旧数据。

验收：

- 同一场比赛 PM market ticks 高频推送时，ASA/GS `SCORE_CHANGED` 仍能立即进入策略。
- tick 事件只触发最新行情更新和持仓回撤检查，不触发比分策略建仓。
- match signal 只触发策略判断，不被 tick 队列阻塞。
- account event 只同步实盘账号、订单、成交，不触发策略建仓。
- `epl-eve-mac-2026-05-04` 重放时，04:27、04:32、04:40、04:42 的 ASA 早于 PM 信号都能被策略看到。

### 4.2 交易模块通用函数

目标：

- 策略和交易员都只能通过交易模块 API 查询和交易。
- 实盘与模拟共用同一套买卖校验逻辑。
- 实盘交易和查询统一走 provider gateway。

任务：

- 实现或整理 `MarketState`：
  - `guid`。
  - teams。
  - score。
  - outcome asset ids。
  - latest ask1/bid1。
  - updated_at_utc。
- 实现查询函数：
  - `get_market(guid)`。
  - `get_pm_match(guid)`。
  - `get_external_match(guid)`。
  - `get_account(trader_id)`。
  - `get_positions(trader_id)`。
  - `provider_get_account(provider, account_alias)`。
  - `provider_get_positions(provider, account_alias)`。
  - `provider_get_orders(provider, account_alias)`。
- 实现交易函数：
  - `buy(intent)`。
  - `sell(intent)`。
  - `close_position(reason)`。
  - `provider_buy(provider, account_alias, order)`。
  - `provider_sell(provider, account_alias, order)`。
- 查询价格时，先读内存最新态，再查 CLOB。
- 买入使用更高 ask1，卖出使用更低 bid1。
- 模拟和实盘只在执行 gateway 层分叉。
- 当前 provider 只实现 PM；KS 只保留接口和配置字段，不接服务、不下单。

验收：

- 策略不能直接读 Redis。
- 策略不能直接调用 provider gateway。
- 实盘交易员账户、持仓、交易记录以 provider 为准；本地 Redis 只做必要缓存。
- 模拟交易员仍使用本地模拟账本。
- 多账户配置能区分 `provider + account_alias`，PM 与后续 KS 可同时存在。

### 4.3 交易员职责

目标：

- 交易员负责账户和风险，不负责策略判断。

任务：

- 交易员订阅 market tick。
- 实盘交易员订阅绑定账号的 account event。
- 每个持仓记录买入价、当前最高 ask1、当前 bid1、数量、投入金额。
- 当持仓方向 ask1 从最高点回落 `stop_loss_drawdown` 绝对值时，交易员强制平仓。
- 收到策略交易指令后，先做通用参数校验：
  - 最大持仓数量。
  - 资金利用率%。
  - 单笔上限%。
  - 单次加仓资金上限%。
  - 单场最多加仓次数。
  - ask1 上限。
  - 85 分钟后金额减半。
- 买入、卖出、不买入都写明确原因。

验收：

- 回撤 0.05 表示价格从最高 ask1 回落 0.05 美元，不是亏损 5%。
- `buy_price=0.23` 且最高 ask1 仍是 0.23 时，不允许因为回撤 0.05 立即卖出。
- 单笔上限 20%、账户总资产 10000 时，单笔金额上限约 2000，再按价格换算整数份。
- 模拟交易员自行维护账户、持仓、交易记录；实盘交易员以 provider 回报同步。

### 4.4 策略职责

目标：

- 策略只输出交易意图，不直接修改账户、持仓、Redis。

任务：

- `football_score_delay_trade` 订阅 match signal。
- 需要时查询交易模块的比赛、盘口、账户、持仓。
- 收到外部比分变化时，用事件里的 PM base 比分快照判断是否领先 PM。
- 外部领先 PM 时：
  - 进球后买入领先方。
  - 扳平时可以买入 Draw。
  - 领先方扩大比分优势时可加仓。
  - 领先方净胜球从 2 变 1 且满足保护条件时发出平仓意图。
- 不满足条件时，发出不交易理由。

验收：

- ASA/GS 早于 PM 的比分信号不因为后续 PM 追上而失效。
- PM 早于或等于外部数据源时不买入。
- ask1 > 0.93 时不建仓、不加仓，但允许平仓。
- 85 分钟后建仓/加仓金额减半。

### 4.5 测试计划

新增测试：

- PM tick 不进入判别器。
- ASA/GS score signal 不被 tick 队列按 `guid` 丢弃。
- 同一 `guid` 下 tick 和 score signal 同时到达时，score signal 在 1 秒内进入策略。
- account event 与 tick、score signal 并发到达时，按账号路由，不阻塞策略。
- 交易员回撤强平按最高 ask1 的绝对回落值触发。
- 策略只返回 intent，不直接改账户。
- CLOB 与 WS 价格差值：
  - `abs(diff) == 0.01` 不写超过提示。
  - `abs(diff) > 0.01` 写提示。
- 买入用更高 ask1，卖出用更低 bid1。
- 实盘交易员展示账户/持仓/订单以 provider 返回为准。
- 多个 PM 账号同时配置时，user WS 回报只更新对应账号交易员。

## 三轮执行提示词

### 第一轮

```text
请阅读 pdt2.1/README.md、pdt2.1/AGENT.MD、pdt2.1/02-requirements.md、pdt2.1/03-system-design.md、pdt2.1/04-redis-data-design.md、pdt2.1/05-development-plan.md，并复制 pdt2.1/frontend-snapshot/ 作为新项目 front/。

执行 PDT2.1 第一轮：搭 Python + Redis + FastAPI + 原前端复制 + 最小 runtime + 空 API + WebSocket heartbeat。不要实现真实 PM/GS/ASA 连接，不要做真实下单。完成后运行测试并汇报。
```

### 第二轮

```text
请阅读 pdt2.1/05-development-plan.md 的第二轮。

执行 PDT2.1 第二轮：允许使用多 Agent，但只使用 Project Manager、Collector、Listener、Trader 四类角色。Collector 只做 PM/GS/ASA HTTP 采集和 guid 匹配；Listener 只做 PM/GS/ASA WS、过滤、标准化、入库和推送；判别器只做比赛数据变化检测；Trader 只做交易模块、交易员、策略 API、约束检查、模拟/真实 dry-run 和默认比分时差策略。不要接 KS/TRD/篮球/PG，不要提交真实订单。
```

### 第三轮

```text
请阅读 pdt2.1/05-development-plan.md 的第三轮。

执行 PDT2.1 第三轮：做完整链路集成、前端联调、Redis TTL/retention、日志脱敏、PM/GS/ASA connectivity check、dry-run soak test 和测试修复。真实交易仍默认 dry-run；只有我单独明确授权后，才允许考虑小额真实订单。
```
