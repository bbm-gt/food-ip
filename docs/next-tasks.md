# 下一任务：Director Core Phase 1

> 更新日期：2026-08-13。本文只定义下一阶段开发范围；本次文档清理不实现任何业务代码、Schema、API、测试或运行时变更。

## 当前主线

**Phase 1 — Director Core 最小骨架**

目标是在不改造旧创作系统的前提下，新建一个与 `CreativeConversation` 独立的 Director Core，为后续 `EXPLORE → DEEPEN → CREATE → REVIEW → READY` 产品主链建立最小、可恢复、可验证的运行骨架。

采用“新内核重做 + 成熟工程底座复用”：旧 creative/scriptgen 系统冻结为 Legacy，既有 Materials、Timeline、FFmpeg、Export 与适用的持久化能力继续保护和复用。

## Phase 1 目标

- `DirectorSession`：定义新内核最小会话边界，不复用旧 `CreativeConversation` 作为新主线状态。
- 五阶段状态机：支持 `EXPLORE`、`DEEPEN`、`CREATE`、`REVIEW`、`READY` 的最小合法流转骨架。
- Orchestrator：接收用户输入、调用当前阶段能力并执行边界内流转；不提前演化为复杂 Router 或 Multi-Agent。
- 独立持久化：Director Core 状态与旧创作会话分开保存，不破坏既有项目数据与兼容读取。
- `client_message_id` 幂等：同一客户端消息重试不会重复推进会话或重复产生副作用。
- 最小 API：只暴露创建/读取会话、提交消息及恢复所需的必要接口。
- 状态恢复：进程或客户端中断后，可以从已持久化状态继续会话。
- 基础测试：覆盖状态流转、持久化/恢复、消息幂等、最小 API 与 Legacy 隔离。

详细 Schema、API 路径、持久化布局与错误契约属于 Phase 1 实施前需要基于现状提出并确认的设计，不在本次 docs-only 更新中提前定案。

## 当前暂不做

- 前端重做；
- Knowledge Retrieval 接入；
- 完整 `CREATE` Prompt；
- 完整 `REVIEW` Prompt；
- 复杂 Memory；
- Vector DB、GraphRAG 或其他复杂 Retrieval 基础设施；
- Multi-Agent 或 Agent 网络；
- 自动剪辑、Timeline、FFmpeg 或 Export 重构。

## Legacy 处理

- 旧 `ResearchProfile → IPProfile → CreativeBrief → TopicCard → ScriptBundle → 固定评分 Review` 路径不再继续修补为新内核。
- 未经明确任务，不删除旧系统，不改变其 Schema、API、持久化或运行行为。
- Legacy 历史完成记录与维护事项以 Git 历史、现有 API 文档、代码和测试为准，不再把它们列为当前产品路线。

## Phase 1 开始前

正式实现前，应先检查当前代码、测试和持久化行为，提出最小设计与兼容影响，并对涉及 Schema、API 与持久化的决策取得用户确认。
