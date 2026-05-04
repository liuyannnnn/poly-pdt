# PDT2.1 简化重构规划包

日期：2026-04-30

## 目标

PDT2.1 是对原项目的减法重构规划。目标不是做一个通用多市场平台，而是把当前可用逻辑按更少代码重写为：

- Python 后端。
- Redis 作为唯一运行存储。
- 前端页面先不改，只保留必要 API 适配。
- base 只接 Polymarket，简称 PM。
- outsource 只接 Goalserve，简称 GS。
- 只做足球。
- 所有 I/O、监听、采集、交易员处理都走 asyncio。

## 核心结论

旧项目主链路是对的：collector、listener、binding、event bus、trader、strategy、execution。问题主要是范围扩大后复杂度叠加：

- 同时考虑 PM、GS、TRD、篮球、PostgreSQL、Timescale、Redis。
- collector/listener/API/storage 为兼容多源和历史数据不断加字段。
- 交易员运行、策略判断、模拟交易和真实交易路径还没有收敛成一个清楚的执行接口。

PDT2.1 的设计只保留一条主链路，并通过 `guid` 建立 PM/GS 唯一匹配索引：

```text
PM/GS collector
  -> PM 主导匹配并生成 guid
  -> PM sports/market/user WS + GS live WS
  -> 过滤当前 guid 比赛
  -> 标准化和变化检测
  -> PM/GS 独立写入 Redis
  -> 广播给正在运行的交易员
  -> strategy 调用 Trader API
  -> simulation 或 real execution
```

多 Agent 只保留四类：Project Manager、Collector、Listener、Trader。

## 阅读顺序

1. `01-original-project-analysis.md`
2. `02-requirements.md`
3. `03-system-design.md`
4. `04-redis-data-design.md`
5. `05-development-plan.md`
6. `06-frontend-handoff.md`
7. `AGENT.MD`
8. `frontend-snapshot/`

## 实施边界

第一版明确不做：

- 不接 KS。
- 不接 TRD。
- 不做篮球。
- 不接 PostgreSQL/Timescale。
- 不做复杂数据湖或通用 source framework。
- 不为了前端显示补假数据。
- 不让 strategy 直接下单、直接读写 Redis、直接访问 PM gateway。

## 参考资料

正式开发 PM 真实交易和 WS 前，应再次核对官方文档：

- [Polymarket authentication](https://docs.polymarket.com/api-reference/authentication)
- [Polymarket create order](https://docs.polymarket.com/developers/CLOB/orders/create-order)
- [Polymarket websocket overview](https://docs.polymarket.com/developers/CLOB/websocket/wss-auth)
- [Polymarket market channel](https://docs.polymarket.com/developers/CLOB/websocket/market-channel)
- [Polymarket user channel](https://docs.polymarket.com/developers/CLOB/websocket/user-channel)

## 前端快照

`frontend-snapshot/` 已包含当前验证过的前端源码，不含 `node_modules` 和 `dist`。

新项目第一轮应直接复制：

```text
pdt2.1/frontend-snapshot/ -> front/
```

前端页面结构和交互先不改。后端字段变化只允许通过 API client / mapper 适配。
