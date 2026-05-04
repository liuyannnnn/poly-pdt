# 原项目理解与简化判断

## 1. 现在的核心业务并不差

原项目已经形成了正确的业务骨架：

- 采集 PM 赛事和盘口。
- 采集 GS 赛事数据。
- 以 PM 为主匹配 GS。
- 监听 PM sports、PM market、PM user、GS live。
- 把监听数据转成统一 trader event。
- 通过 event bus 广播给运行中的交易员。
- 交易员实例化策略。
- 策略返回交易信号。
- execution 做模拟成交、持仓、账户和日志。
- 前端通过 matches、ticks、accounts、positions、trades、logs、WS 展示。

这些边界应保留，不需要推翻。

## 2. 复杂度主要来自范围膨胀

旧 Rust 版本把很多“以后可能需要”的内容提前放进来了：

- 多外部源：GS 和 TRD 并存。
- 多运动：足球和篮球并存。
- 多存储：PostgreSQL、Timescale、Redis 并存。
- 多数据表：当前态、历史态、tick、log、binding、source payload 分散到很多表。
- 多 listener：PM sports、PM market、PM user、GS、TRD market、TRD event poll 同时存在。
- 多兼容字段：为了兼容历史前端和历史 schema，字段越写越多。

这些内容不是方向错误，而是不适合现在的第一版 Python 重构。

## 3. PDT2.1 应该保留的东西

保留：

- PM 是 base，系统内部比赛 ID 以 PM 事件为核心。
- GS 只是 outsource，GS 数据不覆盖 PM 数据。
- collector 负责 HTTP 当前态和低频兜底。
- listener 负责 live 推送。
- 收到什么就保存什么，raw payload 和标准化字段分开存。
- 标准化事件广播给交易员。
- 交易员之间隔离运行。
- strategy 只判断事件并产出交易意图。
- execution 是唯一处理下单、成交、持仓、收益、日志的地方。

## 4. PDT2.1 应该删除的东西

删除：

- KS 相关设计。
- TRD 相关路径。
- 篮球。
- PostgreSQL/Timescale/migration。
- 通用 outsource 切换层。
- 大量分层 repository。
- 复杂多轮上线提示词。
- 过早的跨市场、多 venue、多腿订单设计。

## 5. 对旧 Python 版本的判断

旧 Python 版本有一个值得保留的简洁形态：

- FastAPI 启动 runtime。
- runtime 持有 collector/listener/trading manager。
- trading instance 有自己的 queue。
- dispatcher 把市场事件发给运行中的交易员。
- 每个交易员用自己的 strategy 和 executor。

PDT2.1 可以采用这个形态，但要修正两点：

- 不再把策略、模拟器、数据源和存储揉在一个 runtime 大对象里。
- 不再用单一 tick 表达所有实时数据，改成 `StandardEvent`，里面同时包含 PM、GS、score、clock、markets、raw refs。

## 6. 关键简化原则

代码量少的前提不是少建文件，而是少建概念。PDT2.1 只保留 7 个概念：

1. PM source。
2. GS source。
3. Match binding。
4. StandardEvent。
5. Trader。
6. Strategy。
7. Execution。

