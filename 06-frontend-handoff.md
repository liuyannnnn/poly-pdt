# 前端交接说明

## 1. 快照位置

当前已验证前端源码已复制到：

```text
pdt2.1/frontend-snapshot/
```

该目录排除了：

- `node_modules`
- `dist`

保留了：

- `package.json`
- `package-lock.json`
- `vite.config.ts`
- `src/`
- 样式文件
- 现有页面组件
- 现有 API client / mapper 测试

## 2. 新项目使用方式

第一轮开发时直接复制：

```text
pdt2.1/frontend-snapshot/ -> front/
```

然后在新项目中运行：

```bash
cd front
npm install
npm run build
```

## 3. 修改边界

页面结构、组件布局和交互先不要改。

允许修改：

- API base URL。
- API client。
- mapper。
- 类型定义。
- 与后端字段对齐所需的测试。

禁止修改：

- 页面布局重做。
- 视觉风格重做。
- 为了显示效果伪造 PM/GS 数据。
- 把缺失 ask1/bid1 补成推算值。

## 4. 后端兼容目标

后端应优先兼容当前页面需要的接口：

- `/api/v1/matches`
- `/api/v1/matches/history`
- `/api/v1/ticks`
- `/api/v1/matches/{guid}/snapshots`
- `/api/v1/external-source/match/{guid}`
- `/api/v1/accounts`
- `/api/v1/positions`
- `/api/v1/trades`
- `/api/v1/logs`
- `/api/v1/tradings`
- `/api/v1/ws/market`

如果后端内部使用 `guid`，前端 mapper 中的 `match_id` 可以映射为 `guid`，但不要改变页面层已经验证过的使用方式。
