# Phase 0.5 — 5-Video Knowledge Quality Pilot · Acceptance Contract

| | |
|---|---|
| **状态** | DRAFT — 待评审接受 |
| **版本** | 0.2（轻量化修订） |
| **日期** | 2026-08-09 |
| **权威依据** | `CLAUDE.md` §16、§17、§19、§20、§11、§5、§6 |
| **前置审计** | Readiness Audit（READY WITH SMALL FIXES；blocker B1/B2） |
| **评审结果** | 已执行（2026-08-10/11）：Phase 0.5 Creative Value Gate = PARTIAL / STRONG POSITIVE（非 PASS），当前 blocker = Fact Boundary。详见 `docs/creative_value_gate/README.md` |

本文件只定义 5 视频 Pilot 的验收规则。不包含任何代码 / Schema / 字段修改，不引入新基础设施。

---

## 0. Purpose

Pilot 只需要回答三个问题：

1. **AI 有没有学对？**（Knowledge Fidelity）
2. **学出来的 Knowledge 有没有实际创作价值？**（Creative Utility）
3. **是否值得继续扩大知识生产？**（Scale 判定）

本契约是轻量的：5 个视频样本太小，不做复杂评分、不设伪精确百分比、不做统计显著性。

---

## 1. Execution Prerequisites

正式运行前，Readiness Audit 的 2 个 blocker 必须已解决（本契约不修复它们）：

- **B1**：`chunk_text` 需与所引 `segment_ids` 的真实段文本对齐，不匹配即拒绝/标记。
- **B2**：`_validate_origin` 不得自动填充 `inference_basis` 或改写 origin；缺失 provenance 由 persisted 校验器拒绝。

> 判定规则本身不依赖任何代码改动。

---

## 2. Gate A — Knowledge Fidelity

### 2.1 逐条检查（人工重点判断）

1. 老师是否真的表达或支持这条知识
2. evidence 是否真实并可回溯（段存在、时间正确）
3. 是否曲解原意
4. 是否改变原话强度（"有时"→"总是"）
5. 条件是否遗漏
6. 例外是否遗漏
7. `explicit` / `inferred` / `synthesized` 是否正确
8. provenance 是否真实
9. 案例是否仍然被当作案例（`source_case_fact`）
10. 是否把个案错误升级成普遍规律
11. 是否污染当前用户餐厅事实

### 2.2 评分

每条重要知识给一个 Fidelity 评分：

```text
0 = 错误 / 不可信
1 = 基本正确，但存在明显遗漏或边界问题
2 = 正确、可追溯、边界基本合理
```

### 2.3 单条状态

```text
PASS         = 评分 2
MINOR ISSUE  = 评分 1（轻微遗漏/表述，结论仍正确）
MAJOR ISSUE  = 评分 0–1（明显错误或边界破坏，但可纠正）
CRITICAL ERROR = 触发下方 Hard Fail 任一 → 该条不可信
```

---

## 3. Hard Fail（只有这些能单条判死）

以下情况**人工确认一次真实发生**，即认为知识系统可信性被破坏 → **Pilot FAIL**：

| 情形 | 判定锚点 |
|---|---|
| fabricated / nonexistent evidence（证据伪造 / 引用不存在的段） | 卡片证据与真实 ASR 段对不上 |
| fabricated provenance（编造 provenance / 分类明显错误） | `inference_basis` 无真实依据、origin 与内容不符 |
| course case fact 被当成当前用户餐厅事实（污染） | 案例餐厅信息被当作 methodology 或用户事实输出 |
| Knowledge 无法可靠回溯到真实 Source / Segment | `chunk_id` → `evidence_segment_ids` → ASR 链断裂 |

单条 CRITICAL ERROR 在**交叉核验确认**后生效（不依赖出现次数）。

---

## 4. 质量问题（单条不判死）

以下问题**单条出现**时记为 `MINOR ISSUE` 或 `MAJOR ISSUE`，写入记录：

- 单案例泛化
- 原话强度改变
- 条件遗漏
- 例外遗漏
- 方法论过度总结
- inference 不合理
- context 丢失

只有当这些问题：

- 反复出现、跨多个视频出现、明显呈系统性，
- 或会直接导致错误 Creative Decision，

才判定整个 Pilot 不通过。**不人为规定次数阈值**——由审核者基于 5 个视频的整体印象判断是否已成"模式"。

---

## 5. Gate B — Creative Utility

**不要求所有 Knowledge 都是 Creative Craft。** 主要观察：methodology、可迁移方法、AntiPattern、CreativeFormat，以及其他明显应指导创作的知识。

对这些知识人工问：

1. 它解决什么创作问题？
2. 什么情况下使用？
3. 能不能告诉 Director 具体应该怎么做？
4. 为什么这么做？
5. 什么情况下不应该这么做？
6. 是否可能改变 Creative Decision？（参考：objective / audience_value / core_material / angle / core_tension / proof / information_flow / business_role / performer_fit / missing_facts / risk_flags）

评分：

```text
0 = 基本只是摘要
1 = 有实际启发，但仍需较多二次推理
2 = 已经能比较明确地指导 Creative Decision
```

**单条 Utility = 0 不代表 Pilot FAIL。** 真正看的是整体信号：

> 5 个视频跑完后，系统是反复产出真正有用的 Creative Craft，还是大多数仍只是课程摘要？

---

## 6. Human Review Procedure（两层，轻量）

### 6.1 Layer 1 — Quick Triage（每个视频）

快速浏览该视频的主要知识产物，寻找：

- 明显幻觉
- evidence 问题
- provenance 问题
- case / methodology 错分
- 明显泛化
- 大量摘要型知识
- 条件 / 例外明显缺失

不逐条打分，只标记可疑/典型条目。

### 6.2 Layer 2 — Deep Review（每个视频）

从 Triage 结果中挑选**少量有代表性的知识**深入审核。**不要只挑最好看的**，应覆盖：

- 好的方法论
- 可疑的方法论
- case
- anti-pattern
- 条件/例外复杂项
- 看起来可能被过度概括的知识

数量不机械写死，原则：**足够让审核者真正看清这个视频的知识质量**。

深审步骤：卡片 → `chunk_id` → `evidence_segment_ids` → ASR 原话（`raw_text`/`corrected_text` + 时间戳）→ 逐项 Fidelity 检查 → 对高价值方法论条目做 Utility 判断。

### 6.3 角色与复核

- 主审 1 人；CRITICAL ERROR 判定须经第二审核者独立复核确认。
- 分歧以从严计，记录分歧理由。

### 6.4 记录

每视频一张简单记录：条目 → Fidelity 0/1/2 → Utility 0/1/2（方法论条目）→ 状态 → 证据追溯是否成功 → 备注。另记录典型好例子 / 典型坏例子。

---

## 7. 5-Video Selection Rules

- **不指定具体视频**（77 视频内容未正式筛选），只为"好处理"而选 → 拒绝。
- 覆盖 ≥1：方法论型 / 案例型 / 方法+案例混合型 / 条件-例外丰富型 / 高风险泛化型（口语化、经验式断言、易压成口号）。
- 5 条全部走**真实生产管线**（transcribe → refine → per-source 持久化 + 全局快照），禁止 pilot-only 管线。
- 来源为现有课程素材；triage 排除 `very_poor`；尽量同领域（餐饮 IP / 烧烤）。
- 每条记录选题理由（对应暴露哪类风险）。

---

## 8. Final 3-Question Gate

5 个视频完成后，最终报告**只回答三个结论**（附最重要证据）：

### 8.1 Did it learn correctly?

整体：**YES / PARTLY / NO**（依据 Fidelity 深审 + Hard Fail 有无）

### 8.2 Is the knowledge creatively useful?

整体：**YES / PARTLY / NO**（是否已出现真正 Creative Craft，还是大多数只是摘要）

### 8.3 Should we scale?

只能选一个：

- **GO** — 学得对 + 出现可用的 Creative Craft，进入下一道 Creative Decision 对照验证
- **FIX FIRST** — 先修 extraction / Schema / Creative Craft 表达，再重新小规模验证
- **STOP** — 当前知识路线本身存在根本问题，需要重新设计

判定以"**是否已出现稳定、重复、可解释的价值信号**"为准，不依赖任何百分比。

---

## 9. Post-Pilot Decision Gate

**即使结果为 GO，也不等于可以直接跑 77 个。**

下一步仍必须执行：

```text
Without Knowledge   vs   WITH relevant Knowledge
（同一 owner 任务 → 两路 Creative Decision 输出 → 比较）
```

只有 Knowledge 确实改善 Creative Decision，才允许考虑扩大知识生产。若改善为无 / 微弱 / 不一致 / 仅表面装饰 → 禁止 77 视频，先调整 extraction / Schema / Creative Craft 表示。

---

## 10. Document Control

| 版本 | 日期 | 变更 |
|---|---|---|
| 0.1 | 2026-08-09 | 初稿：两层验收 + 人工审核 + 选择规则 + Gate |
| 0.2 | 2026-08-09 | 轻量化：删百分比/次数阈值，Hard Fail 收敛为 4 类，审核简化为两层，终判收敛为 3 结论 |

与 `CLAUDE.md` 冲突时以 `CLAUDE.md` 为准。
