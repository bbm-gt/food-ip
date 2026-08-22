# Food-IP 项目交接

> 更新日期：2026-08-22。接手顺序：先读 `AGENTS.md` 与 `.codex/skills/food-ip-engineer/SKILL.md`，再读 `docs/architecture.md`、Architecture Amendment 002、Architecture Amendment 003 和 `docs/next-tasks.md`。既有 API 与实现细节按需查 `docs/api.md`、代码和测试。

## 当前产品定位

Food-IP 的长期目标是成为**餐饮老板的长期 AI 内容编导**：持续发现值得拍的内容，挖掘真实素材，创作自然有吸引力的视频，并在受控上下文支持下越来越懂老板和店铺。

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
5. Director Core 的 Session、五阶段编排、六表持久化、幂等、恢复、最小 API 和聊天前端已经实现。
6. 当前正式任务是 **Script Core Product Rework**：双入口、一个首推与两个备选、单问题追问、后台 REVIEW、只交付标题与口播稿。
7. 长期关系层与单条 `DirectorSession` 分离；当前不做自动学习、主动推荐、轻档案、内容历史或制作链路。
8. 旧系统暂时不删除，冻结为 **Legacy / compatibility**，继续保护既有 API、数据和项目。

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

Script Core Product Rework 已完成自动化合同与兼容回归。下一步由用户启动本地服务，判断真实方向、追问、自然表达、吸引力和约 30–60 秒口播效果；在收到验证结论前停止扩展。Phase 1I 文件继续保留但转入 Deferred；轻档案与内容记录只有在脚本内核通过后才实施。详细动态状态见 `docs/project-status.yaml` 与生成的 `docs/next-tasks.md`。

## 接手纪律

- 修改前先执行 `git status --short`，保护用户已有改动。
- 涉及 Schema、API、持久化、架构、Fact / Knowledge 边界或兼容性时，先检查现状、说明方案与权衡，并等待确认。
- 不把 Legacy 实现误写成未来主线，也不把尚未通过用户本地验证的脚本质量描述为已经通过。
- 运行与改动范围匹配的测试或构建，完成前检查最终 diff 与 git status。
- 未经明确授权，不删除旧系统、不改 secrets / `.env`、不 commit、不 push。
