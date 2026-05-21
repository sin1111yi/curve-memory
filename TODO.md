# Curve Memory — 待办事项

## P1 — 应该做

| # | 内容 | 说明 | 依赖 |
|---|------|------|------|
| 8 | cron 事件通知 | 归档事件能通知用户（Hermes cron hook） | Hermes 平台侧支持 |
| 10 | 环境变量覆盖 | 已实现 ✅ | — |

## P2 — 可以做

| # | 内容 | 说明 | 复杂度 |
|---|------|------|--------|
| 2 | 检索 pagination | 当前固定 top-5，大数据量不够用 | 低 |
| 3 | inotify 实时索引 | 文件变更立即索引，不等 cron | 高（依赖系统 inotify） |
| 6 | JSON/YAML 输出标准化 | 所有命令支持 --json | 中 |
| 7 | 互斥锁日志增强 | 锁冲突时记录详情 | 低 |
| 8 | 多 profile 支持 | 不同 Hermes profile 用不同记忆库 | 高 |
| 9 | 记忆标签/分类 | 给 topic 打标签，按分类检索 | 中 |
|  | batch touch/forget/mature --all | 批量操作 | 低（CLI 参数支持） |

## 已完成（本轮）

- [x] P1#1 recover 命令
- [x] P1#2 undo + 操作日志
- [x] P1#3 锁文件超时清理
- [x] P1#5 config CLI
- [x] P1#6 prefetch 诊断
- [x] P1#7 stats 统计
- [x] P1#9 deactivate/activate
- [x] P1#11 setup 自动注册 cron
- [x] P2#1 export 导出
- [x] P2#4 batch touch
- [x] P2#5 plot ASCII 图
- [x] P2#10 stats 详细统计
