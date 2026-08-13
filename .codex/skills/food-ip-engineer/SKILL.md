---
name: food-ip-engineer
description: 用于开发和维护 Food-IP「餐饮老板的 AI 内容编导」，覆盖 EXPLORE、DEEPEN、CREATE、REVIEW、READY 产品主链，以及前后端、知识生产、素材、时间轴和 FFmpeg 导出。适用于需要遵守 Owner Facts / Knowledge 边界、既有 API 与持久化兼容、知识证据约束和工程验证纪律的任务。
---

# 项目定位

Food-IP 是餐饮老板的 AI 内容编导。

当前产品主链：

```text
EXPLORE
→ DEEPEN
→ CREATE
→ REVIEW
→ READY
```

`REVIEW` 必须先判断根因，再决定回退位置：

```text
Writing Problem → CREATE
Material Problem → DEEPEN
Direction Problem → EXPLORE
```

Workflow 只控制关键边界，LLM 负责具体判断。不要把产品做成固定问卷、固定评分体系、复杂 Router、Multi-Agent 或固定问题树。

## 五阶段工程语义

- `EXPLORE`：找到当前最值得继续的内容方向；不能由固定内容桶或 legacy strategy 代替判断。
- `DEEPEN`：只补最影响最终内容质量的真实素材，不为完整表单而追问。
- `DEEPEN → CREATE`：已有真实素材足以支撑核心表达，不需要靠编造、模板或空话撑内容。
- `CREATE`：基于方向、真实素材和必要上下文形成符合老板表达、可实际制作的内容。
- `REVIEW`：先诊断 Writing / Material / Direction 根因，再回到对应阶段；不能默认直接 Rewrite。
- `READY`：内容连贯、事实有边界，并达到可拍摄交付状态。

Memory、Knowledge、历史内容、上传素材与外部信息均按需调用，不是固定流程节点。外部热点只能提供内容机会，不能代替老板自己的真实内容。

# Owner Facts 与 Knowledge 边界

Knowledge 教 AI 怎么判断，不能创造 Owner Facts，也不能证明当前餐厅发生了什么。

- Owner Facts 只能来自老板明确提供的事实或明确可信、已确认的来源。
- 不得把知识案例、常见规律、历史相似内容、外部信息或创意建议写成当前老板的经营事实。
- 缺失事实只有在显著影响当前创作判断时才追问，并且只问最少必要问题。
- 不必追问的细节应保持未确认或使用条件式表达，不能为了生动而编造。
- 创意方向、角度、结构与表达属于 AI 判断，必须与 Owner Facts 保持可区分。

该边界是当前主线的运行原则，不再描述为等待 Knowledge System 成熟后才启用的 Deferred 能力。具体 Schema、校验或兼容改造仍属于需确认的重大设计，不能自行新增。

# Monorepo 边界

- `backend/`：产品后端和运行时能力。
- `frontend/`：面向老板的 Web 客户端。
- `knowledge_pipeline/`：独立 Creative Knowledge 生产子系统。
- `docs/`：架构、API、产品决策、部署和项目文档。
- `runtime/`：本地运行数据，不当作源代码。

产品运行时与 `knowledge_pipeline/` 逻辑分离。不要把知识摄入内部实现直接耦合进产品运行时；任何新稳定合约、Persistence 或 Retrieval 设计都必须先获确认。

# Legacy 与复用边界

现有 Script Engine、`ResearchProfile`、legacy `BossInfo`、`IPProfile`、`CreativeConversation`、`CreativeBrief`、`TopicCard`、`ScriptBundle`、Writer / Review 工具、素材、时间轴、FFmpeg 与导出流程，属于 legacy、compatibility 或可复用能力，不是未来产品主链。

- 未经明确授权，不删除或破坏既有能力和已有项目数据。
- 优先复用真正适合新主线的模块，但不能为了复用旧系统牺牲最终产品效果。
- 不要默认把 TopicCard、多候选 ScriptBundle、固定 strategy、内容桶或固定 Review 分数接入新主线。
- legacy 的候选保存、版本、API 和持久化行为继续兼容；只有明确任务才改变其契约。
- 素材、时间轴与 FFmpeg 导出是可复用生产能力，应继续保护。

# REVIEW 开发规则

REVIEW 的核心职责是根因诊断，而不是固定维度打分或默认局部改写。

- Writing Problem：内容方向和真实素材足够，问题在表达、结构或成稿，返回 `CREATE`。
- Material Problem：方向可继续，但真实素材不足以支撑核心表达，返回 `DEEPEN`。
- Direction Problem：当前方向本身不值得继续或无法成立，返回 `EXPLORE`。
- 程序合规、真实性和结构校验可以作为保护边界，但不能用固定总分代替 LLM 的根因判断。
- REVIEW 不得改变 Owner Facts，也不得把外部热点或知识案例补成老板事实。

现有 Director Review、评分字段或局部修稿能力只作为 legacy / 可复用实现事实保留，不作为新主线的固定运行逻辑。

# Knowledge Pipeline 规则

`knowledge_pipeline/` 继续作为独立知识生产子系统保留，但不是当前唯一主线。现有课程视频 pipeline 是已经实现并验证的一条摄入路径，不代表全部未来知识来源。

除非出现具体回归、测试失败、不变量被破坏或用户明确要求，不重开已完成的可靠性工作。保留：

- timestamp authority；
- stable deterministic identities；
- evidence / provenance；
- strict schema validation；
- crash / resume；
- idempotency；
- per-source persistence；
- atomic global snapshots；
- fail-fast validation。

不得把尚未确认的知识源分层、准入规则、证据标准、时效治理、评分、Retrieval 或评估方案写成既定设计。知识管线的专属实现与测试规则以 `knowledge_pipeline/AGENTS.md` 为准。

# 决策权限

以下重大变化必须先检查现状、说明选项与权衡、给出建议，并等待用户确认：

- 产品架构或 Workflow 关键边界；
- Schema / 数据契约；
- Owner Facts / Knowledge 边界语义；
- Persistence、存储或 Retrieval 策略；
- 模型 / provider 策略；
- 重大依赖；
- 路线、阶段或成本；
- 验证与验收标准；
- 兼容性保证。

不要自行新增 Schema、Persistence 设计、Router、Agent、评分机制、新状态或 Retrieval 架构。`.codex/agents/*.toml` 只有在用户明确要求时才能修改。

# 任务执行协议

## 1. 开始前

- 先运行 `git status --short`，识别并保护用户已有修改。
- 阅读任务涉及的代码、测试、API 与文档，不根据名称猜实现。
- 用户当前指令和已确认产品决策决定目标方向；代码与测试说明当前已实现行为；发现冲突时报告，不把旧实现误当未来路线。
- 只读取完成任务所需的项目文件，避免无关范围扩张。

## 2. 修改前

- 梳理当前实现、涉及模块、数据流、前后端影响、兼容边界和风险。
- 如果触及重大决策，先停下获取确认。
- 已确认边界内优先选择最小、清晰、可验证的改动。

## 3. 实现纪律

- 优先修改和复用现有模块，避免平行系统、重复抽象与无关重构。
- 保持 API 兼容和旧项目数据可读，除非任务明确改变契约。
- 新增或改变行为时补充相应测试。
- AI 输出必须经过与风险相称的程序约束，不能直接覆盖真实业务数据或生成未确认事实。
- 外部付费 AI 调用在自动化测试中必须 mock，除非用户明确要求受控集成测试。
- 不删除文件、不修改 secrets / `.env`、不 commit 或 push，除非用户明确要求。

## 4. 前端边界

修改前端前，先检查当前页面流程、项目状态和刷新恢复实现。避免随意增加全局状态、复制业务状态或为了新主线无关重构。

## 5. 视频与导出兼容

视频相关修改必须保护：

- `shot` 编号一致性；
- `backend/app/engine/timeline.py` 的时长权威；
- materials / upload 流程；
- FFmpeg 导出流程。

不要在前端或多个模块重复维护最终时长逻辑。

## 6. 验证与完成

使用与改动范围匹配的最窄测试迭代，再运行受影响组件的完整检查。

后端：

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/app/tests -q --basetemp .pytest-basetemp
```

前端：

```powershell
cd frontend
npm.cmd run build
```

Knowledge pipeline：

```powershell
cd knowledge_pipeline
python -m pytest -q
```

完成前必须检查：

- 测试 / build / type / lint 结果；
- `git diff`；
- `git status --short`；
- 兼容性影响；
- 是否存在无关改动。

最终汇报实际修改、验证结果、兼容性与剩余风险，不把未实现能力描述为已完成。

# Project-specific references

No bundled scripts, references, or assets are required. Read the relevant project files directly when a task needs them.
