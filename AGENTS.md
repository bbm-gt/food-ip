# Food-IP Knowledge Pipeline — 维护指南

本仓库是**专业 Creative Knowledge 系统**（课程视频 → 结构化、可追溯、有边界的知识），最终服务 `food-ip` 产品的 Creative Decision。

> 工程规则、阶段权威依据与详细约定以 `CLAUDE.md` 为准；本文档是任务执行者的短入口。

## 开始工作

修改前依次阅读本文件、`CLAUDE.md`、相关代码与测试，先执行 `git status --short` 保护工作区已有修改。不从零搭建或重构既有流程。

测试（从仓库根目录）：

```bash
python -m pytest -q
# 或
python -m unittest test_food_ip_p0.py
```

外部付费 AI 调用在自动化测试中一律 mock。

## 双仓库关系

```text
本仓库（food-ip-knowledge-pipeline）= 专业 Creative Knowledge 系统
food-ip（另一仓库）                = 老板最终使用的产品
```

Knowledge 只教 AI **怎么判断**，不能告诉 AI **当前老板实际上发生了什么**（见"事实边界"）。

## 当前阶段状态

```text
P0 Reliability                 = FINAL GO / CLOSED（勿重新折腾）
5-video Knowledge Fidelity     = 基本验证成功
Knowledge Creative Value       = Strong Positive Signal
Phase 0.5 Creative Value Gate  = PARTIAL / STRONG POSITIVE（非 PASS）
current blocker                = Fact Boundary（事实边界）
下一阶段方向                   = Minimal Creative Decision / Fact Boundary
                                + Minimal Retrieval Validation
```

## 事实边界（最重要原则）

区分三类：

```text
confirmed_facts   = 老板明确提供，或可信 Memory 中已确认的事实
creative_decision = AI 的创作判断与建议
missing_facts     = 创作需要但尚未确认的信息
```

Knowledge 不能充当当前老板事实；未确认信息标"需确认"或"如果事实成立，可以这样拍"。边界由 system / validation 层强制，不能指望知识本身提供。

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
test_food_ip_p0.py                               P0 可靠性测试基线（77 项）
docs/PHASE_0_5_5_VIDEO_PILOT.md                  5-video pilot 验收契约
docs/creative_value_gate/                        4 场景 A/B 评估档案（Gate = PARTIAL / STRONG POSITIVE）
```

生产链路：视频 → transcribe → refine → per-source 持久化 → 全局快照（chunks / knowledge_cards / case_cards / anti_patterns / creative_formats 五个协调文件，原子重建）。Source 是持久化主单位；全局文件是可重建索引。

## 禁止事项

- **不构建 Multi-Agent**；优先 Workflow + structured modules。
- **不开始**：77-video 全量摄入、GraphRAG、Neo4j、RAPTOR、Embedding/向量库、Content Engine V2、Director Agent。
- **不重新折腾已完成的 P0**（无回归、无违规、无明确任务时不重审）。
- **不改弱验证**：不伪造 evidence / provenance / identity；原子持久化、崩溃恢复、幂等重跑不变。
- 不新增临时调试/报告文件，不重复生成状态报告；保持仓库整洁。

## 完成任务后

运行相关测试，检查 `git status --short` 与 `git diff --stat`；仅在明确授权时 commit / push。说明修改文件、验证结果、兼容处理和剩余风险。
