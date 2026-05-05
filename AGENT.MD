# PDT2.1 Agent Rules

## 项目范围

只实现：

- Python。
- Redis。
- PM as base。
- GS/ASA as outsource。
- Football only。
- Existing frontend layout unchanged。
- Frontend source comes from `pdt2.1/frontend-snapshot/`。

禁止加入：

- 当前版本不实现 KS 服务接入或真实下单。
- TRD。
- Basketball。
- PostgreSQL。
- Timescale。
- Generic multi-provider framework。
- Complex multi-round live trading workflow。

## Agent 角色

### Project Manager Agent

职责：

- 控制项目目标与范围。
- 协调 Collector、Listener、Trader。
- 确保开发进度。
- 确保功能完整性。
- 确保 UI 效果一致性。
- 控制代码层级和逻辑简洁性。

### Collector Agent

只负责采集模块：

- PM HTTP 采集。
- GS `home` / `d1` HTTP 采集。
- ASA HTTP 采集或查询近期足球比赛。
- 以 PM 为基准匹配 GS/ASA。
- 生成 `guid`。
- 存 PM 当前态、GS/ASA 当前态、binding。
- 设置比赛相关 TTL 3 天。

禁止：

- 不接 WS。
- 不推送交易员。
- 不写策略。
- 不下单。

### Listener Agent

只负责实时监听和分发：

- PM sports WS。
- PM market WS。
- PM user WS。
- GS live WS。
- ASA live WS。
- 过滤非系统当前比赛。
- 标准化实时字段。
- 入 Redis。
- 推送前端。
- PM market ticks 推送交易模块行情通道。
- PM sports/GS/ASA 比赛数据推送判别器。
- PM user 账号、订单、成交事件按 `provider + account_alias` 推送对应交易员。

禁止：

- 不做 HTTP 采集。
- 不做初始匹配。
- 不写策略。
- 不下单。

### Trader Agent

只负责交易模块：

- 模拟交易员。
- 真实交易员 dry-run/真实路径骨架。
- provider gateway 交易和查询接口，当前只实现 PM，后续可扩展 KS。
- 交易员实例。
- 策略实例。
- 策略可调用 API。
- 交易日志和运行日志。
- 盘口、资产、持仓、余额查询。
- 买入/卖出。
- 通用约束检查。
- 默认策略 `football_score_delay_trade`。
- 三类隔离事件入口：
  - market tick。
  - match signal。
  - account event。

禁止：

- 不接 PM/GS/ASA HTTP。
- 不接 PM/GS/ASA WS。
- 不修改前端布局。

### Discriminator Component

判别器不是额外 Agent，只是 Listener 和 Trader 之间的组件。

只负责：

- 接收 PM sports、GS、ASA 的比赛数据。
- 记录每个 `source + guid` 的最新比赛值。
- 对比分、点球、红黄牌等字段做简单变化检测。
- 只把比赛变化信号推送给交易模块。

禁止：

- 不处理 PM market ticks。
- 不查账户。
- 不执行策略。
- 不下单。
- 不改写历史日志。

## 工作原则

- 先保持代码少。
- 先让主链路跑通。
- 组件边界清楚，但不要过度分层。
- 所有 I/O 用 async。
- PM 数据和 GS/ASA 数据逻辑独立、存储独立。
- 必须通过 `guid` 建立 PM/GS/ASA 唯一匹配索引。
- 不能为了前端显示编造数据。
- Strategy 只能调用 Trader API。
- Trader 是唯一处理买入、卖出、账户、持仓、订单、成交的模块。
- Redis 是唯一存储。
- PM market tick 与比赛变化信号必须隔离，不能共用会按 `guid` 丢信号的队列。
- 账号事件必须按 `provider + account_alias` 路由到对应交易员，不进入判别器、不进入策略。
- 模拟交易员自行维护账户、持仓、交易记录；实盘交易员以 provider 查询和 user WS 回报为准。
- 系统可配置多个 PM 账户；后续接 KS 时沿用 provider/account 路由，不改策略边界。
- Redis 中的日志、交易记录、ALL/LIVE/ticks 时序都按追加方式写入，不回写、不补点、不拟合。

## 数据原则

- raw payload 原样保存。
- 标准化字段单独保存。
- PM 字段和 GS/ASA 字段互不覆盖。
- 凭证只放 `.env`，不进 Redis、不进日志、不进 fixture。

## 三轮开发顺序

### 第一轮：骨架

- Python/FastAPI 项目骨架。
- Redis store。
- 复制 `pdt2.1/frontend-snapshot/` 为新项目 `front/`。
- 空 API。
- Runtime 启停。
- WebSocket heartbeat。

### 第二轮：模块开发

- Collector：PM/GS/ASA HTTP + guid 匹配。
- Listener：PM/GS/ASA WS + 标准化 + 入库 + 推送。
- Discriminator：比赛数据变化检测 + match signal。
- Trader：交易模块 + 交易员 + 策略 API + 约束检查 + simulation + real dry-run + 默认策略。

### 第三轮：集成测试

- Collector -> Redis -> API。
- Listener -> Redis -> Frontend WS。
- PM market ticks -> Trading MarketState -> Trader market tick。
- 比赛数据 -> Discriminator -> Trader match signal。
- PM user -> Account router -> Trader account event。
- Trader -> Redis -> accounts/positions/trades/logs。
- PM user WS -> account/order/fill reconciliation。
- Redis TTL/retention。
- credential leakage scan。
- connectivity check。
- dry-run soak test。

## 每个 Agent 回复格式

```text
完成内容：
- ...

改动范围：
- ...

验证：
- ...

未完成/风险：
- ...
```
