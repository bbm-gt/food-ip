# Food-IP 目标架构

> 本文描述新 Director Core 的目标边界，不表示其已经实现。当前已实现的旧创作系统继续作为 Legacy / compatibility 保留；具体既有 API 行为以 `docs/api.md`、代码和测试为准。

## 产品定位与主链

Food-IP 是**餐饮老板的 AI 内容编导**。目标架构为：

```text
用户
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

## 核心原则

### Workflow 管边界，AI 管判断

Workflow 只约束阶段职责、必要条件和允许的流转。方向选择、素材价值、表达结构与根因诊断由 AI 在这些边界内做具体判断。固定问卷、固定评分、复杂 Router 或固定问题树不能成为新主线的核心逻辑。

### Owner Facts 与 Knowledge 严格分离

- Owner Facts 只能来自老板明确提供的信息，或其他明确可信且已确认的来源。
- Knowledge 教 AI 如何判断，不能证明当前老板或餐厅发生了什么。
- AI 推测、案例、历史相似内容、外部信息与创意建议不得升级为 Owner Facts。
- 缺少的事实只有在实质影响当前创作判断时才追问，并且只问最少必要问题；否则保持未确认或使用条件式表达。

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

Director Core 将作为独立内核建设，与旧 `CreativeConversation` 分开持久化和演进。具体持久化 Schema、API 合约及迁移策略只在相应阶段获得确认后设计。

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

- Director Core 的复杂 Schema 或完整数据契约；
- 复杂 Memory、Memory 治理或自动长期记忆；
- Retrieval 架构、Vector DB、GraphRAG 或其他检索基础设施；
- Knowledge 准入、分层、评分或时效治理方案；
- Agent 拆分、Multi-Agent 协作或复杂 Router；
- 模型/provider 策略和重大新依赖。

Director Core 的第一阶段范围见 `docs/next-tasks.md`。
