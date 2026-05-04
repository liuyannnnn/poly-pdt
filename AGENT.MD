# PDT2.1 Agent Rules

## 项目范围

只实现：

- Python。
- Redis。
- PM as base。
- GS as outsource。
- Football only。
- Existing frontend layout unchanged。
- Frontend source comes from `pdt2.1/frontend-snapshot/`。

禁止加入：

- KS。
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
- 以 PM 为基准匹配 GS。
- 生成 `guid`。
- 存 PM 当前态、GS 当前态、binding。
- 设置 TTL 5 天。

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
- 过滤非系统当前比赛。
- 标准化实时字段。
- 检测变化。
- 入 Redis。
- 推送 Trader 和前端。

禁止：

- 不做 HTTP 采集。
- 不做初始匹配。
- 不写策略。
- 不下单。

### Trader Agent

只负责交易通用模块：

- 模拟交易员。
- 真实交易员 dry-run/真实路径骨架。
- 策略可调用 API。
- 交易日志和运行日志。
- 盘口、资产、持仓、余额查询。
- 买入/卖出。
- 通用约束检查。
- 默认策略 `football_score_delay_trade`。

禁止：

- 不接 PM/GS HTTP。
- 不接 PM/GS WS。
- 不修改前端布局。

## 工作原则

- 先保持代码少。
- 先让主链路跑通。
- 组件边界清楚，但不要过度分层。
- 所有 I/O 用 async。
- PM 数据和 GS 数据逻辑独立、存储独立。
- 必须通过 `guid` 建立 PM/GS 唯一匹配索引。
- 不能为了前端显示编造数据。
- Strategy 只能调用 Trader API。
- Trader 是唯一处理买入、卖出、账户、持仓、订单、成交的模块。
- Redis 是唯一存储。

## 数据原则

- raw payload 原样保存。
- 标准化字段单独保存。
- PM 字段和 GS 字段互不覆盖。
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

- Collector：PM/GS HTTP + guid 匹配。
- Listener：PM/GS WS + 标准化 + 变化检测 + 推送。
- Trader：策略 API + 约束检查 + simulation + real dry-run + 默认策略。

### 第三轮：集成测试

- Collector -> Redis -> API。
- Listener -> Redis -> Frontend WS -> Trader queue。
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
