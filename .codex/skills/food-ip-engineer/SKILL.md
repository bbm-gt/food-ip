---
name: food-ip-engineer
description: 用于开发、审查和维护 Food-IP「餐饮老板的 AI 内容编导」相关任务。始终遵守产品定位、五阶段主链、Owner Facts / Knowledge 边界、Director Core 与 Legacy 隔离及重大决策确认纪律；仅在任务直接涉及对应子系统时读取本 Skill 的专项 references。
---

# Food-IP 最小核心规则

Food-IP 的长期目标是成为餐饮老板的 AI 内容编导：持续发现值得拍的内容，挖掘真实素材，创作自然有吸引力的视频，并在受控、可确认的长期上下文支持下越来越懂老板和店铺。当前实施重点仍是老板主动发起的单条口播脚本内核，不实现自动学习、主动推荐或制作链路。

主链保持为：

```text
EXPLORE → DEEPEN → CREATE → REVIEW → READY
```

Workflow 只控制关键边界，AI 负责具体创意判断。`REVIEW` 先判断根因再决定回退：Writing Problem → `CREATE`，Material Problem → `DEEPEN`，Direction Problem → `EXPLORE`。不要把主线固化为问卷、固定评分、问题树、Router 或 Multi-Agent 系统。

## 始终适用的边界

- Knowledge、AI 推断、案例、热点和外部信息只能帮助判断，不能创造或证明当前餐厅的 Owner Facts。
- “越来越懂”必须来自老板确认或明确可信来源的可治理上下文；AI 推断不得因重复出现或长期使用而自动升级为 Owner Facts。
- Owner Facts 必须来自老板明确提供，或来自明确可信且已确认的来源。缺失事实只有在显著影响当前判断时才最少追问；其余保持未确认或条件式表达，不为生动而编造。
- 老板用自然语言否定或更正事实时，由大模型理解语义；当前有效事实直接替换，不用关键词树判断，也不为该事实更正新增隐藏的 Rejected Fact 或 supersedes 记录。
- Knowledge、Memory、历史内容、上传素材和外部信息按需调用，不是固定 Workflow 节点。
- Director Core 与 Legacy 创作系统隔离。新主线不能被旧模型、固定策略或旧流程牵着走；需要复用时通过明确 Adapter 或稳定边界连接，不以复用牺牲最终产品效果。
- 老板前台保持简单，不暴露内部 Stage、Agent、Router、Memory 等实现概念；后台复杂度必须藏在清晰、稳定的产品边界后。
- 不引入未经证明的复杂架构、状态、Schema、Persistence、Retrieval、模型策略或固定评分机制。

## 重大决策与执行范围

- 涉及架构或 Workflow 边界、Schema / API / 数据契约、事实语义、存储 / Retrieval、模型 / provider、重大依赖、路线 / 成本、验收标准或兼容性保证时：先检查现状，说明选项、权衡和建议，等待用户确认。
- 新增或改变行为时补充相称测试；先运行与改动范围匹配的最窄检查，再运行受影响组件的完整检查。
- 开始任务先检查 `git status --short`，只读取和修改当前任务需要的范围，保护无关用户改动；不要把旧实现或临时状态自动当成未来设计。
- 修改后报告：实际修改的文件、相关检查及结果、兼容性影响、剩余风险，以及 commit / push 是否发生。除非用户明确要求，不自行 commit 或 push。

# 按需读取专项规则

只读取与当前任务直接相关的 reference；不要默认读取全部文件。各 reference 只由本文件直接指向，不互相引用或递归加载。

| 任务范围 | 按需读取 |
| --- | --- |
| `DirectorSession`、Orchestrator、主链行为、Director Core 的 Schema / API / 持久化或事实处理 | [references/director-core.md](references/director-core.md) |
| `knowledge_pipeline/`、知识摄入、证据、快照或 Knowledge 可靠性 | [references/knowledge-system.md](references/knowledge-system.md) |
| 旧 Script Engine、`CreativeConversation`、`TopicCard`、`ScriptBundle`、旧 Writer / Review 或兼容修复 | [references/legacy-compatibility.md](references/legacy-compatibility.md) |
| 素材、上传、时间轴、shot、FFmpeg、导出或相关前台适配 | [references/production-adapter.md](references/production-adapter.md) |
| REVIEW 诊断、测试、验收、回归或验证策略 | [references/testing-review.md](references/testing-review.md) |

简单的 Git 状态检查、文件查找、普通文档整理等不触及上述范围的任务，只使用本核心规则，并按任务需要读取项目自身的最小文件集。
