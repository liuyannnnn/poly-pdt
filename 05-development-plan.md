# PDT2.1 三轮开发计划

## 目标

用最少可维护代码实现 PM + GS + 足球 + Redis + 异步交易主链路。

## Agent 分工

本项目只使用 4 类 Agent。

1. Project Manager Agent：控制目标、范围、进度、UI 一致性、代码层级和逻辑简洁性。
2. Collector Agent：只做 PM/GS HTTP 采集和 PM 主导匹配。
3. Listener Agent：只做 PM/GS WS 监听、过滤、标准化、变化检测、入库、推送。
4. Trader Agent：只做交易通用模块、策略 API、约束检查、模拟/真实交易、默认策略。

不要再拆 Source/Matcher/Normalizer/API/QA 等额外常驻角色。需要测试和前端兼容时，由 Project Manager 分配到对应 Agent 的范围内完成。

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
- 以 PM 为基准匹配 GS。
- 生成 `guid`。
- 保存 PM 当前态、GS 当前态、binding。
- TTL 5 天。

任务：

- 实现 PM HTTP client/parser/collector。
- 实现 GS HTTP client/parser/collector。
- 实现队名归一化。
- 实现 PM 主导匹配。
- 写 `pm:match:{guid}`。
- 写 `gs:match:{guid}`。
- 写 `binding:{guid}`。
- 写 PM/GS/guid 索引。
- 写 collector run report。

验收：

- PM collector fixture test。
- GS collector fixture test。
- matcher test：直接匹配、队名+时间、pending。
- TTL 5 天测试。
- PM 和 GS 数据不互相覆盖。

### 2.2 Listener Agent

目标：

- 接 PM sports WS。
- 接 PM market WS。
- 接 PM user WS。
- 接 GS live WS。
- 只保留系统当前存在的 `guid`。
- 标准化实时字段。
- 检测变化。
- 入 Redis。
- 推送给 Trader 和前端。

任务：

- 实现 PM sports parser/listener。
- 实现 PM market parser/listener，更新 moneyline ask1/bid1。
- 实现 PM user parser/listener，更新用户账户数值并推送前端。
- 实现 GS live parser/listener。
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
- 实现变化检测。
- unknown guid 进入 dead-letter。

验收：

- parser fixture test。
- unknown guid 不推送交易员。
- PM 字段不被 GS 覆盖。
- GS 字段不被 PM 覆盖。
- Market WS 能更新 home/draw/away ask1/bid1。
- User WS 能更新账户状态并推送前端。

### 2.3 Trader Agent

目标：

- 支持模拟交易员和真实交易员。
- 每个交易员关联一个策略和一个账户。
- 为策略提供统一 API。
- 执行前做通用约束检查。
- 实现默认策略 `football_score_delay_trade`。

任务：

- 实现 TraderManager。
- 实现每个 Trader 独立 asyncio queue。
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
  - 查询 PM/GS 当前数据。
- 实现通用参数：
  - 最大持仓数。
  - 资金利用率%。
  - 单笔上限%。
  - 最多加仓次数。
  - 加仓资金上限%。
  - 自动止损回撤值，默认 0.05。
- 实现 TradingValidator。
- 实现 SimulationEngine。
- 实现 PM real gateway dry-run 骨架。
- 实现 `football_score_delay_trade`。

默认策略验收：

- GS 比分早于 PM 时触发买入。
- 新信号与持仓方向不一致时立即反手。
- 85 分钟后不建仓，只平仓。
- ask1 > 0.9 不建仓。
- ask1 > 0.95 不加仓。
- 持仓方向 ask1 > 0.95 且其他方向比分变化时立即平仓。

通用交易验收：

- 多个 trader 同时运行互不影响。
- 单个 trader 异常不影响其他 trader。
- buy 使用 ask1。
- sell 使用 bid1。
- 余额不足拒单。
- 资金使用率超限拒单。
- 最大持仓数超限拒单。
- 加仓次数超限拒单。
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
- 接通 Listener -> Redis -> Frontend WS -> Trader queue。
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
- 模拟交易至少跑通一场 fixture 或真实 live replay。
- 前端 matches/detail/accounts/positions/trades/logs 都能读到真实后端数据。
- PM/GS 通过 guid 唯一关联。
- PM/GS 存储独立，互不覆盖。
- Redis 中无 PM/GS 密钥、token、签名原文。
- dry-run 下不会提交真实订单。
- 日志可定位 source、guid、trader_id、order_id。
- 所有测试、lint、前端 build 通过。

## 三轮执行提示词

### 第一轮

```text
请阅读 pdt2.1/README.md、pdt2.1/AGENT.MD、pdt2.1/02-requirements.md、pdt2.1/03-system-design.md、pdt2.1/04-redis-data-design.md、pdt2.1/05-development-plan.md，并复制 pdt2.1/frontend-snapshot/ 作为新项目 front/。

执行 PDT2.1 第一轮：搭 Python + Redis + FastAPI + 原前端复制 + 最小 runtime + 空 API + WebSocket heartbeat。不要实现真实 PM/GS 连接，不要做真实下单。完成后运行测试并汇报。
```

### 第二轮

```text
请阅读 pdt2.1/05-development-plan.md 的第二轮。

执行 PDT2.1 第二轮：允许使用多 Agent，但只使用 Project Manager、Collector、Listener、Trader 四类角色。Collector 只做 PM/GS HTTP 采集和 guid 匹配；Listener 只做 PM/GS WS、过滤、标准化、变化检测、入库和推送；Trader 只做交易通用模块、策略 API、约束检查、模拟/真实 dry-run 和默认比分时差策略。不要接 KS/TRD/篮球/PG，不要提交真实订单。
```

### 第三轮

```text
请阅读 pdt2.1/05-development-plan.md 的第三轮。

执行 PDT2.1 第三轮：做完整链路集成、前端联调、Redis TTL/retention、日志脱敏、PM/GS connectivity check、dry-run soak test 和测试修复。真实交易仍默认 dry-run；只有我单独明确授权后，才允许考虑小额真实订单。
```
