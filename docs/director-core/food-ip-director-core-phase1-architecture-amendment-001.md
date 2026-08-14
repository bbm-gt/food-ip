# Food-IP Director Core Phase 1 Architecture Amendment 001

文档状态：FINAL PASS

## 1. 修订范围与权威关系

本 Amendment 只修订 `food-ip-director-core-phase1-core-architecture-final.md` 中关于 Context Checkpoint 的生成方式、内容边界和使用时机。它不修改六张核心表、字段、生命周期、成功 Turn 原子性、Working State 权威性、Evidence 语义或 Phase 1 范围。Schema FINAL 中关于 `director_context_checkpoints` 的列、类型、索引、覆盖边界、不可变性和合法 JSON 对象要求继续有效；本 Amendment 只定义该既有 TEXT 容器所承载的执行层 JSON v1 语义。

正式产品设计文档的基线顺序为：Core Architecture FINAL PASS；Minimal SQLite Schema FINAL PASS；本 Amendment 001；Execution Contract。Amendment 001 是对 Core Architecture 中 Checkpoint 旧执行条款的明确、范围限定的修订：在 Checkpoint 范围内覆盖“只能确定性结构化压缩、不使用自由文本 AI 摘要”的旧表述，但不覆盖 Schema 的存储边界或字段决定。`AGENTS.md` 是仓库治理约束，任何产品设计文档都不能授权违反它；Skill 仅是执行辅助，不能覆盖正式产品设计。若 Skill 与正式设计文档在设计语义上冲突，以正式设计文档为准；若无冲突则继续遵循 Skill 的执行辅助要求。本执行契约不得扩大 Amendment 的范围。

## 2. Checkpoint 的不变边界

Context Checkpoint 仍然是历史上下文的派生缓存，不是业务状态或事实来源：

- 非权威；Working State、Raw Transcript 和成功 Turn snapshot 始终优先；
- 可废弃、可重建，损坏或冲突不改变其他权威数据；
- 不参与成功 Turn 权威事务，不在该事务内生成或写入；
- 不得覆盖 Working State，不得推进 Stage、版本或 Session 生命周期；
- 不得作为 Evidence Reference，不得支撑 Owner Fact、Owner Constraint 或 Direction；
- Checkpoint 生成、校验、写入或废弃失败不得影响已成功 Turn；
- 不保存隐藏推理、原始模型响应、prompt、工具内部消息或自由日志。

## 3. 允许事务后模型生成结构化 Checkpoint

在以下边界内，允许调用模型生成 Checkpoint：

- 只读取已经成功提交的 `director_messages`、`director_turns`、`director_working_state`，以及用于定位范围的已提交 Checkpoint；
- 只能在权威事务提交之后，或主 Turn 模型调用前确实需要压缩历史时生成；不得进入成功 Turn 的 SQLite 写事务；
- 输出必须通过严格 Context Checkpoint JSON v1 校验；关键语义条目必须携带覆盖范围内已提交 Message 的 `message_refs`；
- `conversation_summary` 只能概括带来源引用的条目，不能独立创造事实；`confirmed_owner_positions` 以及任何老板事实、约束或立场必须引用 OWNER Message；
- 不得创造新的 Owner Fact、Owner Constraint 或 Direction，不得把模型判断直接写入 Working State；
- 与 Working State 或 Raw Transcript 冲突时立即废弃，不得尝试覆盖或“修正”权威数据；
- 生成失败、Schema 校验失败、provenance 校验失败或写入失败均只影响 Checkpoint，不影响成功 Turn。

模型生成的内容是带来源引用的语义压缩缓存，不是事实权威、不是状态权威、不是可直接写入 Working State 的模型判断，也不能替代被引用的证据原文。上下文组装仍须按 Evidence Reference 定点回取受保护的 OWNER 原文。

## 4. 按需生成，不固定每 Turn 生成

系统不得在每个成功 Turn 后固定调用 Checkpoint 模型。只有出现以下任一情况时才按需生成、重建或更新：

- 当前没有有效 Checkpoint；
- 有效 Checkpoint 的 `covered_through_seq` 明显落后于可用历史；
- 如果不压缩历史，下一次 Model Context Assembly 将超过配置化上下文预算；
- 需要重建已损坏、已废弃或 provenance/hash 校验失败的 Checkpoint。

按需生成仍必须以完整成功 Turn 边界为覆盖边界；不能覆盖半个回合，不能跳过 Evidence 定点回取，不能因为存在 Checkpoint 就裁剪当前 OWNER Message 或当前 Working State。

## 5. 与原架构的关系

本修订不引入第七张表、第三方依赖、队列、Outbox、Lease、Memory、Knowledge、Multi-Agent、PostgreSQL 或通用 Agent Runtime。Checkpoint 的模型生成只是事务后的可失败缓存构建步骤，不改变 Director Core 的五阶段主链和成功 Turn 精确消息/事务契约。
