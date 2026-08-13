# Food-IP 项目交接

> 更新日期：2026-08-13。接手顺序：先读 `AGENTS.md` 与 `.codex/skills/food-ip-engineer/SKILL.md`，再读 `docs/architecture.md` 和 `docs/next-tasks.md`。既有 API 与实现细节按需查 `docs/api.md`、代码和测试。

## 当前产品定位

Food-IP 是**餐饮老板的 AI 内容编导**。

面向用户的目标体验：

```text
发现 → 深挖 → 判断 → 定方向 → 再深挖 → 创作 → 自检 → 可拍
```

内部产品主状态：

```text
EXPLORE → DEEPEN → CREATE → REVIEW → READY
```

Workflow 管关键边界，AI 管边界内的具体创作判断。`REVIEW` 先判断 Writing / Material / Direction 根因，再分别返回 `CREATE` / `DEEPEN` / `EXPLORE`。

## 已确认的最新决策

1. 旧创作内核不再继续修补为未来产品主线。
2. 采用“**新内核重做 + 成熟工程底座复用**”。
3. 新 Director Core 与旧 `CreativeConversation` 独立，不依赖 `CreativeBrief`、`TopicCard` 或 `ScriptBundle`。
4. 新内核五阶段统一为 `EXPLORE → DEEPEN → CREATE → REVIEW → READY`。
5. 下一项正式任务是 **Director Core Phase 1 — 最小骨架**。
6. 旧系统暂时不删除，冻结为 **Legacy / compatibility**，继续保护既有 API、数据和项目。

## 目标架构

```text
用户
→ Director Orchestrator
→ EXPLORE / DEEPEN / CREATE / REVIEW / READY
→ ReadyContent
→ Production Adapter
→ Materials / Timeline / FFmpeg / Export
```

Director Core 按需读取最少必要 Context，不默认注入完整 `ResearchProfile`、`IPProfile`、Memory 或 Knowledge。Owner Facts 必须来自老板或其他明确可信、已确认的来源；Knowledge 和 AI 推测不能成为当前餐厅事实。

当前不采用复杂 Multi-Agent，也不提前设计复杂 Schema、Memory、Retrieval、Vector DB 或 Agent 架构。

## Legacy 与成熟底座

旧 `ResearchProfile → IPProfile → CreativeBrief → TopicCard → ScriptBundle → 固定评分 Review` 主线是当前已实现能力的一部分，但只作为 Legacy、兼容基线和必要维护对象，不再决定未来产品架构。

继续复用和保护：

- Materials / Upload；
- Timeline，且 `backend/app/engine/timeline.py` 仍为时长权威；
- FFmpeg / Export；
- 适用的持久化基础能力；
- 既有 REST API、旧项目数据和脚本兼容。

`knowledge_pipeline/` 是独立 Creative Knowledge 生产子系统。其现有可靠性、证据、来源、幂等和原子持久化规则继续有效，但它不代表整个 Food-IP 产品当前主线。

## 下一项正式任务

Director Core Phase 1 的目标范围：

- `DirectorSession`；
- 五阶段状态机；
- Orchestrator；
- 独立持久化；
- `client_message_id` 幂等；
- 最小 API；
- 状态恢复；
- 基础测试。

当前不做前端重做、Knowledge Retrieval、完整 CREATE/REVIEW Prompt、复杂 Memory、Vector DB、Multi-Agent 或自动剪辑重构。详细边界见 `docs/next-tasks.md`。

## 接手纪律

- 修改前先执行 `git status --short`，保护用户已有改动。
- 涉及 Schema、API、持久化、架构、Fact / Knowledge 边界或兼容性时，先检查现状、说明方案与权衡，并等待确认。
- 不把 Legacy 实现误写成未来主线，也不把尚未实现的 Director Core 描述为已经交付。
- 运行与改动范围匹配的测试或构建，完成前检查最终 diff 与 git status。
- 未经明确授权，不删除旧系统、不改 secrets / `.env`、不 commit、不 push。
