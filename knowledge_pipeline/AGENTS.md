# Food-IP Knowledge Pipeline — 维护指南

`knowledge_pipeline/` 是 Food-IP 仓库中的**独立 Creative Knowledge 生产子系统**。本文件中的“当前阶段”“当前工作”与“当前主线”均只指该子系统，不代表整个 Food-IP 产品当前主线；产品主线以父级 `AGENTS.md` 与 `docs/architecture.md` 为准。

仓库当前正式主线是 **Director Core Phase 1 — 最小骨架**。`knowledge_pipeline/` 是独立的 Creative Knowledge 生产子系统，不代表整个仓库的当前主线；只有在明确授权时，才维护或继续优化该子系统。

现有课程视频 pipeline 是一条已经实现并验证的知识摄入路径（课程视频 → 结构化、可追溯、有边界的知识），不是全部知识来源。未来知识源范围与治理方案尚未确认，不在本文件中预设。

> 本文件是 `knowledge_pipeline/` 子系统的工程规则入口。详细架构、验收记录与历史阶段信息以该子系统相关 `docs/` 文档为准；全仓库规范以父级 `AGENTS.md` 为最高权威。

## 开始工作

修改前依次阅读本文件、与当前任务相关的 `docs/`、相关代码与测试，先执行 `git status --short` 保护工作区已有修改。不从零搭建或重构既有流程。

测试（从 `knowledge_pipeline/` 目录运行）：

```bash
cd knowledge_pipeline
python -m pytest -q
# 或
python -m unittest test_food_ip_p0.py
```

外部付费 AI 调用在自动化测试中一律 mock。

## 单仓库边界

```text
food-ip/knowledge_pipeline/ = 专业 Creative Knowledge 系统
food-ip/backend/            = 老板最终使用的产品后端
food-ip/frontend/           = 老板最终使用的产品前端
```

本文件只约束 `knowledge_pipeline/` 子系统；全仓库工程规则以父级
`food-ip/AGENTS.md` 为最高权威。

Knowledge 只教 AI **怎么判断**，不能告诉 AI **当前老板实际上发生了什么**（见"事实边界"）。

## 当前阶段状态

```text
P0 Reliability                 = FINAL GO / CLOSED（勿重新折腾）
5-video Knowledge Fidelity     = 基本验证成功
Knowledge Creative Value       = Strong Positive Signal
Phase 0.5 Creative Value Gate  = PARTIAL / STRONG POSITIVE（非 PASS）
以上为历史验收状态，不是当前 blocker
仓库当前正式主线               = Director Core Phase 1 — 最小骨架
knowledge_pipeline 当前定位     = 独立子系统；仅在明确授权时维护或优化
Owner Facts / Knowledge 边界原则 = 当前立即生效
正式 Fact Contract 与程序实现    = 当前 Deferred
```

## 事实边界原则（当前生效；正式运行时契约 Deferred）

区分三类：

```text
confirmed_facts   = 老板明确提供，或可信 Memory 中已确认的事实
creative_decision = AI 的创作判断与建议
missing_facts     = 创作需要但尚未确认的信息
```

Owner Facts 与 Knowledge 必须分离。Knowledge 不能充当或创造当前老板事实，AI 推测也不能升级为 Owner Facts；未确认信息标"需确认"或"如果事实成立，可以这样拍"。老板信息不足且某个事实确实会实质影响当前创作判断时，只做最少量关键追问；不重要的信息不追问，也不能编造。

以上边界原则当前立即生效。仍属 Deferred 的是正式 Fact Contract Schema、程序级 Fact Validation、Persistence Contract、兼容迁移与运行时具体实现；这些能力未来应由 system / validation 层强制，当前不自行实现。

## 目录与架构

```text
food_ip_transcribe / food_ip_direct_transcribe   Whisper 转写（单视频 → 权威 ASRSegment）
food_ip_refine                                    语义分块 + 知识抽取（principle/technique/anti_pattern/creative_format/operation）
food_ip_models / food_ip_persistence             Pydantic 模型 + 原子持久化 + 全局快照
food_ip_segments / semantic_chunker              分段 / 语义分块
food_ip_whisper_adapter                           faster-whisper 适配
knowledge_graph                                  知识图（基础层）
food_ip_config / food_ip_schemas / export_schemas  配置 / schema 导出
robust_json_parser                                LLM 输出健壮解析
test_food_ip_p0.py                               P0 / Phase 0.5 可靠性测试；具体测试数量以当前实际 pytest 结果为准
docs/PHASE_0_5_5_VIDEO_PILOT.md                  5-video pilot 验收契约
docs/creative_value_gate/                        4 场景 A/B 评估档案（Gate = PARTIAL / STRONG POSITIVE）
```

当前已验证的生产链路：视频 → transcribe → refine → per-source 持久化 → 全局快照（chunks / knowledge_cards / case_cards / anti_patterns / creative_formats 五个协调文件，原子重建）。Source 是持久化主单位；全局文件是可重建索引。该链路只代表现有视频摄入路径，不规定未来知识源范围。

## 决策边界

涉及以下重大变更时，必须先检查现状、说明方案与权衡，并等待用户明确确认后再实施：

- 架构或模块边界
- Schema / 数据契约 / Fact Boundary 语义
- 存储、检索或模型/provider 策略
- 阶段路线、成本或验证/验收标准
- 兼容性保证或其他会明显改变产品行为的关键决策

已确认边界内的普通实现细节和小修复可直接推进。

## 禁止事项

- **不构建 Multi-Agent**；优先 Workflow + structured modules。
- **不实现当前 Deferred 的正式事实契约与运行时机制**：Fact Contract Schema、程序级 Fact Validation、Persistence Contract、兼容迁移或具体运行时实现；当前 Owner Facts / Knowledge 边界原则仍必须遵守。
- 未经明确批准，不新增 Creative Decision Schema、Memory、Retrieval 基础设施、GraphRAG、Neo4j、RAPTOR、复杂 Vector DB、Content Engine V2、Director Agent 或 Multi-Agent 产品架构。
- 不把约 77 个视频或任何未确认的来源清单写成 Knowledge System 的全部范围，也不把尚未确认的准入、评分、证据或时效规则写成既定方案。
- **不重新折腾已完成的 P0**（无回归、无违规、无明确任务时不重审）。
- **不改弱验证**：不伪造 evidence / provenance / identity；原子持久化、崩溃恢复、幂等重跑不变。
- 不新增临时调试/报告文件，不重复生成状态报告；保持仓库整洁。

## 完成任务后

运行相关测试，检查 `git status --short` 与 `git diff --stat`；仅在明确授权时 commit / push。说明修改文件、验证结果、兼容处理和剩余风险。
