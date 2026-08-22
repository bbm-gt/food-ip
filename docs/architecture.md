# Food-IP 目标架构

> 本文同时描述长期目标边界与当前已实现的 Director Core 基础。现有 Session、五阶段编排、六表持久化、幂等、恢复、最小 API 和聊天前端已经实现；创意效果与产品交互正在继续优化。旧创作系统仍作为 Legacy / compatibility 保留；具体 API 行为以 `docs/api.md`、代码和测试为准。

## 产品定位与主链

Food-IP 的长期目标是成为**餐饮老板的长期 AI 内容编导**：持续发现值得拍的内容、挖掘真实素材、创作自然有吸引力的视频，并在受控上下文支持下越来越懂老板和店铺。目标架构为：

```text
用户
→ 长期关系上下文层（按需）
→ Director Orchestrator
→ EXPLORE / DEEPEN / CREATE / REVIEW / READY
→ ReadyContent
→ Production Adapter
→ Materials / Timeline / FFmpeg / Export
```

五个内部状态承担以下职责：

- `EXPLORE`：发现当前最值得继续的内容方向。
- `DEEPEN`：只补充最影响最终内容质量的真实素材。
- `CREATE`：基于已确认方向和真实素材形成可制作内容。
- `REVIEW`：先诊断根因，再决定继续前进或返回对应阶段。
- `READY`：内容连贯、事实有边界，并达到可拍交付状态。

`REVIEW` 的返回关系是：

```text
Writing Problem   → CREATE
Material Problem  → DEEPEN
Direction Problem → EXPLORE
```

`ReadyContent` 是 Director Core 与制作能力之间的目标交付边界名称；本轮不预设其详细 Schema。`Production Adapter` 负责把可拍内容适配到现有素材、时间轴、渲染和导出能力，而不是让新创作内核直接依赖旧脚本对象。

长期关系上下文层与单条内容 Session 相互独立。一个 `DirectorSession` 只服务一条具体内容并在 `READY` 后结束；轻档案、内容历史和未来受控记忆可以按需提供背景，但不能成为第二套创作状态机，也不能把 AI 推断自动升级为 Owner Facts。当前版本仍聚焦老板主动发起的单条口播脚本，不包含自动学习、主动推荐和制作链路。

## 核心原则

### Workflow 管边界，AI 管判断

Workflow 只约束阶段职责、必要条件和允许的流转。方向选择、素材价值、表达结构与根因诊断由 AI 在这些边界内做具体判断。固定问卷、固定评分、复杂 Router 或固定问题树不能成为新主线的核心逻辑。

### Owner Facts 与 Knowledge 严格分离

- Owner Facts 只能来自老板明确提供的信息，或其他明确可信且已确认的来源。
- Knowledge 教 AI 如何判断，不能证明当前老板或餐厅发生了什么。
- AI 推测、案例、历史相似内容、外部信息与创意建议不得升级为 Owner Facts。
- 缺少的事实只有在实质影响当前创作判断时才追问，并且只问最少必要问题；否则保持未确认或使用条件式表达。
- 老板用自然语言纠正事实时，由模型理解否定与替换语义；当前有效事实直接更新，不为该事实更正额外建立隐藏的拒绝或 supersedes 记录。
- 模型可把老板本轮明确表达的事实忠实整理成完整 statement，不要求整理结果逐字包含于原话；应用保留本轮连续原话作为 Evidence，不因非逐字表达而自动降级为未确认信息。
- REVIEW 基于当前事实、约束、未确认推断、Draft 和按需 Knowledge 做语义审核，不以字面相同作为事实成立条件；Knowledge 仍不能证明当前餐厅事实。

### Context 按需读取

Director Orchestrator 根据当前决策读取少量必要 Context。新主线不默认注入完整 `ResearchProfile`、`IPProfile`、历史内容、Knowledge 或 Memory，也不把它们设置为固定流程节点。

### 新旧创作内核解耦

新 Director Core 不依赖 `CreativeConversation`、`CreativeBrief`、`TopicCard` 或 `ScriptBundle`。这些结构及 `ResearchProfile`、`IPProfile`、固定 strategy、内容桶、候选脚本和固定评分 Review 继续服务旧系统兼容，但不参与新主线的创作判断或路由。

### 当前不采用复杂 Multi-Agent

Director Orchestrator 与五阶段 Workflow 是当前方案边界。不要把复杂 Multi-Agent、Agent 网络或多层 Router 提前设计成产品基础设施。

## 系统边界

```text
Director Core
  负责：五阶段状态、创作编排、事实边界、可拍内容交付

Production Adapter
  负责：把 ReadyContent 连接到成熟制作底座

Legacy creative/scriptgen
  负责：现有项目、API、持久化数据与旧脚本流程兼容

knowledge_pipeline/
  负责：独立生产有证据、有来源边界的 Creative Knowledge
```

Director Core 已作为独立内核实现，并与旧 `CreativeConversation` 分开持久化和演进。当前使用已确认的六表 SQLite 契约；后续关系上下文层的 Schema、API 和治理仍需单独确认，不能塞入现有 `DirectorSession`。

`knowledge_pipeline/` 与产品运行时逻辑分离。它可以在未来通过已确认的稳定合约向产品提供按需 Knowledge，但现有 ingestion 内部实现不直接耦合进 Director Core。

## Legacy 与复用边界

旧 `ResearchProfile → IPProfile → CreativeBrief → TopicCard → ScriptBundle → 固定评分 Review` 主线已冻结为 Legacy，不再继续作为新架构基础，也不在本阶段删除。

继续保护：

- 既有 REST API 与已持久化项目；
- `ResearchProfile` / legacy `BossInfo` 兼容；
- 旧脚本与 `ScriptBundle` 兼容；
- Materials 与 Upload 工作流；
- Timeline 行为，且 `backend/app/engine/timeline.py` 仍是时长权威；
- FFmpeg 与 Export 流程；
- 旧脚本生成能力。

复用成熟底座不等于把旧创作对象带入 Director Core。新内核通过明确的适配边界连接生产能力。

## 暂不提前决定

在获得明确产品或架构确认前，不在目标架构中预设：

- 长期关系上下文层的 Schema、同步和治理契约；
- 复杂 Memory、Memory 治理或自动长期记忆；
- Retrieval 架构、Vector DB、GraphRAG 或其他检索基础设施；
- Knowledge 准入、分层、评分或时效治理方案；
- Agent 拆分、Multi-Agent 协作或复杂 Router；
- 模型/provider 策略和重大新依赖。

Director Core 的第一阶段范围见 `docs/next-tasks.md`。
