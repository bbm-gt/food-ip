# Director Core Phase 1 核心架构定稿

> **Architecture Amendment 001：** Checkpoint 的执行边界、允许的事务后模型生成、严格 JSON v1/provenance 校验和按需生成规则以 `food-ip-director-core-phase1-architecture-amendment-001.md` 为准。该 Amendment 只修订本文的 Checkpoint 执行语义，不修改六表 Schema 存储边界；除 Amendment 明确修订的 Checkpoint 条款外，本文主体保持有效。

## 1. 核心结论

Director Core Phase 1 采用 **SQLite、四层上下文、六张核心表**。一个 `DirectorSession` 只服务一次具体内容创作任务，内部由 EXPLORE、DEEPEN、CREATE、REVIEW、READY 五阶段驱动；进入 READY 后永久结束，后续修改通过引用原 `ReadyContent` 的新 Session 完成。

架构的三个权威必须严格分开：Raw Transcript 是成功 Turn 已提交的老板与 AI 可见消息原始证据；Director Working State 是当前创作任务状态的唯一权威；`director_turns` 是每轮成功处理的幂等、过程、提交审计与确定性恢复依据。每个成功 Turn 必须保存足够的信息，使系统无需重新调用模型，也能确定性恢复该 Turn 提交后的 Working State。Checkpoint 只是可重建的历史前缀压缩，Model Context Assembly 只是每个内部步骤调用前临时生成的模型输入，两者都不是事实或状态权威。

系统允许一次老板消息触发多个内部阶段，但每次自动推进都必须经过合法转移和 Content Readiness Gate。流程保持为一个有边界的内容编导内核，不扩张为通用 Agent Runtime，不引入固定问卷、固定评分、复杂 Router、Multi-Agent、Knowledge Retrieval、Memory 或任务系统。

## 2. 已确认产品运行逻辑

老板面对的是自然连续对话，内部 Stage 不直接暴露给老板。阶段状态机固定为：

```text
EXPLORE → EXPLORE | DEEPEN
DEEPEN  → DEEPEN | CREATE
CREATE  → REVIEW
REVIEW  → READY | CREATE | DEEPEN | EXPLORE
READY   → 无
```

阶段处理器的结构化结果必须分离四类含义：第一，运行控制决定本轮是等待老板、继续内部执行，还是已经产出最终内容，对应 `WAIT_FOR_OWNER`、`CONTINUE`、`READY`；第二，目标 Stage 表示当前步骤完成后进入哪个合法阶段；第三，阶段转移原因解释为何保持、前进或回退；第四，Gate 结论或 REVIEW 根因诊断说明内容是否通过以及问题属于表达、素材还是方向。运行控制不能兼任阶段路由，具体字段名和枚举留到 Schema 阶段确定。

因此，REVIEW 的结构化结果必须能够无歧义表达：因表达问题转入 CREATE、因素材不足转入 DEEPEN、因方向问题转入 EXPLORE，或因审核通过转入 READY。`WAIT_FOR_OWNER` 表示当前判断确实缺少会影响方向或内容质量的老板输入；`CONTINUE` 表示无需老板补充，Orchestrator 立即执行结果中指定的合法目标阶段；`READY` 表示最终内容已通过门禁并形成不可变的 ReadyContent。

因此，一条补足关键素材的老板消息可以在同一请求内依次完成 DEEPEN、CREATE、REVIEW、READY，不要求老板发送“继续”。REVIEW 发现表达问题时回 CREATE，素材不足时回 DEEPEN，方向失效时回 EXPLORE。内部自动推进必须有配置化的有限步骤上限；具体默认值留到执行契约和模拟测试阶段确认。超过上限时整轮失败，不提交任何部分权威结果。

READY 后不 reopen 原 Session，也不覆盖原 ReadyContent。需要修改时创建新 Session，并记录其来源 `source_ready_content_id`，从而同时保留原任务、原结果与新创作链路。

## 3. 四层上下文模型

### Raw Transcript

Raw Transcript 追加保存**已经成功完成整个 Turn 并在权威事务中原子提交**的老板和 AI 编导可见消息，保留稳定顺序。它用于界面展示、审计、纠错、Owner Facts 证据追溯和故障恢复。消息一旦成功提交便不因摘要、纠正或压缩而覆盖；老板后来纠正事实，只改变当前 Working State 中该事实的有效性，不改写旧消息。

客户端刚发送、但服务端尚未成功完成处理的消息不属于服务端 Raw Transcript。模型失败、校验失败或事务失败的请求不写入 Raw Transcript；前端可以把这类本地消息显示为 pending 或 failed。客户端使用同一幂等标识重试并成功后，该消息才与本轮 AI 可见回复一起进入 Raw Transcript。失败请求的技术日志和诊断不由 Raw Transcript 承担。

Raw Transcript 不保存模型隐藏推理、内部阶段过程、幂等响应、当前事实投影或自由文本摘要。它是“实际说过什么”的权威，不是“当前应该相信什么”的权威。

### Director Working State

Working State 是当前创作任务的唯一状态权威，承载当前 Stage、已认可方向、创作所需材料状态、当前草稿或评审结论的必要投影，并明确分隔五类内容：Owner Facts、AI Judgments、Unconfirmed Inferences、Rejected Items、Owner Constraints。

Owner Facts 与可追溯的 Evidence Reference 相连；AI Judgments 是方向、结构、表达等编导判断；Unconfirmed Inferences 是尚未得到老板确认的推测；Rejected Items 保存已否定、已纠正或不再采用的内容，防止其被重新混入；Owner Constraints 保存老板明确提出的表达、拍摄或禁用边界。Working State 不复制完整对话，也不承担原文展示和单轮幂等。

### Context Checkpoint

Checkpoint 只对较早、连续的对话历史前缀做确定性结构化压缩，保留已经发生的对话进展，例如过去询问过什么、老板已作何选择、哪些分支已经放弃。它不成为 Owner Facts 证据，不复制完整当前 Working State，不保存覆盖边界之后的消息，也不使用自由文本 AI 摘要。

Checkpoint 是派生缓存：可以校验、废弃并从 Raw Transcript 与成功 Turns 重建。其损坏不能改变 Raw Transcript、Working State 或 ReadyContent。

### Model Context Assembly

Model Context Assembly 不作为持久化真相，而是在每次模型调用前临时组装：

```text
固定规则与事实边界
+ 当前 Working State 的必要投影
+ 最新有效 Checkpoint
+ 最近完整回合
+ 按需读取的早期证据原文
+ 当前老板消息
```

模型默认不读取完整历史。长对话由 Checkpoint 承担旧过程压缩，最近若干完整回合保持原文；任何仍支撑活跃 Owner Fact、老板认可方向或关键约束的早期消息，可按证据引用定点取回原文。这样既控制上下文长度，又不让摘要替代事实证据。

## 4. 六张核心表的职责关系

`director_sessions` 是创作任务根实体，负责 Workspace、Project、来源 ReadyContent、生命周期及创建/结束关系。它不内嵌完整消息、Working State 或幂等记录。

`director_messages` 是 Raw Transcript，按 Session 追加老板与 AI 的可见消息，形成稳定消息序列。成功提交后的消息不原地改写；纠错通过新消息和 Working State 的有效性变化表达。

`director_working_state` 是每个 Session 的可变当前投影，也是 Stage、Owner Facts、判断、推断、拒绝项与约束的唯一当前权威。每轮成功后以新状态版本替换当前投影，但历史过程由 Turns 和 Transcript 保留。

`director_turns` 是按 Session 追加的成功轮次记录，取代简单 `processed_messages`。它承担 `client_message_id` 幂等、本轮前后 Working State 版本、本轮内部阶段过程、结构化结果、首次 API 响应快照，以及单轮追溯与恢复依据。架构不变量是：每个成功 Turn 都必须包含足以确定性恢复其提交后 Working State 的信息，恢复过程不得重新调用模型。最终采用完整 post-state snapshot、版本化 state patch，还是 snapshot 与 patch 组合，留到 Schema 阶段确认；仅保存版本号或含义模糊的结构化结果不满足恢复要求。相同 ID、相同规范化请求直接回放首次响应；相同 ID、不同请求拒绝。失败轮次不写入。

`director_context_checkpoints` 保存可验证、可重建的历史前缀压缩。它按覆盖边界形成版本化记录，最新有效记录参与上下文组装；它不覆盖消息，也不更新 Working State。Checkpoint 不参加成功 Turn 的权威事务，只能在该事务提交成功后生成，并通过独立的 best-effort 短事务写入。生成、校验或写入失败不回滚成功 Turn，后续可以从权威数据重建。

`director_ready_content` 保存 READY 的最终内容产物。一个完成的 Session 关联其 ReadyContent；ReadyContent 保持不可变。后续修改由新 Session 引用旧 ReadyContent，不修改原记录。

其中 Messages、Turns、Checkpoints 和 ReadyContent 采用追加或不可变语义；Working State 是唯一可变当前投影；Sessions 是关系与生命周期根。六表已经分别覆盖原始证据、当前状态、单轮审计、历史压缩和最终产物。再增加通用 Event 表会重复 Messages 与 Turns 的审计职责，并诱导内核演变成通用事件或 Agent 平台，Phase 1 没有这种需要。

## 5. Owner Facts 与方向证据边界

Owner Fact 必须关联可追溯的 Evidence Reference。Phase 1 只启用 owner message evidence：事实必须来自老板明确陈述或明确确认的消息，并能追溯到一条或多条老板原始消息。证据引用的抽象边界不永久写死为某个 `owner_message_id` 字段；未来可以在另行确认后扩展 owner-confirmed material 或 trusted structured source。模型可整理表达，但不能扩大证据原意。老板的拍摄偏好、禁忌和表达要求属于 Owner Constraints，不应混入经营事实。

AI 推断、AI 建议、Knowledge、行业案例、外部热点、Checkpoint 文本和历史相似内容绝对不能自动成为当前老板或餐厅的经营事实。Knowledge 只能教 AI 如何判断；Checkpoint 只能说明旧对话过程；AI 推断只能停留在 Unconfirmed Inferences。它们若影响核心内容，必须由老板消息明确确认后才能提升为 Owner Fact。

老板纠正事实时，Raw Transcript 保留新旧原文。Working State 将旧事实移出有效 Owner Facts，记录为被纠正或拒绝，并让新事实关联纠正消息的 Evidence Reference。后续上下文只把新事实作为当前真相，同时可按需读取旧证据解释纠正过程。

AI 在 EXPLORE 提出的方向只是 AI Judgment。只有老板通过可追溯消息明确认可，Working State 才能把它标记为当前认可方向；因此 `EXPLORE → DEEPEN` 必须同时满足“方向已被老板认可”和“存在对应老板消息证据”。AI 不能通过自己的重复建议或 Checkpoint 摘要替老板确认。

## 6. Checkpoint 与 Working State 边界

Working State 回答“现在任务处于什么状态、当前哪些事实和约束有效”；Checkpoint 回答“较早的对话过程发生过什么”。前者是当前真相，后者是历史压缩。两者同时进入模型上下文时，Working State 的当前结论优先，Checkpoint 不重复完整事实、草稿或阶段投影，只补充已覆盖历史中仍有用的过程信息。若两者冲突，Checkpoint 视为失效，不得覆盖 Working State。

`covered_through_seq` 表示该 Checkpoint 完整覆盖的 Raw Transcript **连续前缀中最后一条消息序号**。覆盖边界只能落在已经成功提交的完整回合末尾，不能越过尚未纳入压缩的消息，不能凭时间戳猜测，也不能包括当前未提交消息。

边界之后的消息全部作为最近历史候选保留原文；至少当前轮之前的若干完整老板—编导回合不得只以 Checkpoint 替代。即使消息位于覆盖边界之前，只要它是活跃 Owner Fact、已认可方向或关键 Owner Constraint 的证据，Context Assembly 仍应按引用取回原文。

Checkpoint 必须具有确定性格式和完整性校验。缺失、版本不支持、覆盖序列不连续或内容损坏时，组装器忽略该 Checkpoint，退回最近原文与按需证据，并可在后续独立重建。Checkpoint 只在包含 Messages、Working State、Turn、可选 ReadyContent 和必要 Session 生命周期变化的权威事务成功后生成，再通过独立 best-effort 短事务写入。它的创建、校验、读取或写入失败不能回滚成功 Turn，也不能污染或降级 Raw Transcript 与 Working State。

## 7. 单轮消息处理流程

高层流程固定为：

```text
获取 Session 锁
→ 授权与幂等预检
→ 读取 Session、Working State、Checkpoint、最近消息
→ 初始化本轮内存候选状态
→ 为当前内部阶段基于最新候选状态重新组装该步骤上下文
→ 调用当前阶段处理器
→ 校验结构化结果
→ 执行 Gate 与合法转移检查
→ 更新内存候选状态
→ 重复内部步骤，直到 WAIT_FOR_OWNER、READY 或超过步骤上限
→ 完整校验
→ 短 SQLite 权威事务一次提交
→ 权威事务成功后 best-effort 生成并独立提交 Checkpoint
```

Session 锁覆盖整轮，防止同一任务并发交错。幂等预检从成功 Turns 判断：重复的同 ID、同请求直接回放，不调用模型；同 ID、不同请求冲突。输入消息先只存在于内存上下文，读取到的已提交数据用于初始化候选状态。每个内部步骤都必须从**最新候选状态**重新组装自己的上下文，而不是整轮只组装一次：CREATE 必须看到本轮 DEEPEN 新增或修正的方向、事实与素材状态，REVIEW 必须看到本轮 CREATE 产生的最新草稿。

当前阶段处理器返回分离了运行控制、目标 Stage、转移原因和 Gate／根因诊断的结构化结果。Orchestrator 校验结果与合法转移，执行对应 Gate，再更新候选 Working State 和本轮内部过程；只有结果要求继续时才使用更新后的候选状态进入下一步骤。达到等待老板、形成 READY 或触发配置化步骤上限时结束循环。

循环成功结束后，系统在内存中生成老板消息、AI 可见消息、Working State 新版本、可确定性恢复的 Turn、可选 ReadyContent 及必要 Session 生命周期变化。完整校验确认 Evidence Reference、合法转移、处理器结果、循环上限、ReadyContent 关系和版本连续性后，才开启短 SQLite 权威事务，一次原子提交上述权威变化。模型调用不放在 SQLite 写事务中，Checkpoint 也不属于该权威事务。

权威事务提交成功后，系统才可以基于已提交数据 best-effort 生成 Checkpoint，并使用独立短事务写入。Checkpoint 的生成、校验或写入失败只意味着缓存暂时缺失，不影响本轮成功，也不得回滚已经提交的 Messages、Working State、Turn、ReadyContent 或 Session 生命周期变化。

任一模型调用、内部处理或校验失败，都不保存老板消息，不改变 Working State，不创建 Turn，不登记成功幂等，也不留下部分 ReadyContent。客户端使用相同 `client_message_id` 重试。Phase 1 不增加 Pending Turn、Outbox、Lease、任务队列或两阶段提交。

## 8. Content Readiness Gate

自动推进不是按 Stage 顺序机械前进。`DEEPEN → CREATE` 前，Gate 至少确认：已有老板认可的明确方向；认可有老板消息证据；真实素材足以支撑核心表达；关键事实有老板消息证据；主体无需依靠编造、套模板或空话补足。任一条件不成立，停在 DEEPEN 等待老板，或者在方向本身失效时回 EXPLORE。

`REVIEW → READY` 前，Gate 至少确认：内容有完整开头、主体和结尾；核心观点得到充分展开；不是两三句话的敷衍产物；包含具体、真实、有表达价值的内容；事实边界清楚；语言自然、像老板说话；实际可拍；没有实质性素材缺口。

未通过时必须诊断根因：表达不足回 CREATE，素材不足回 DEEPEN，方向失效回 EXPLORE。Gate 是结构化判断边界和证据约束，不是固定总分、固定问卷或固定问题树。每次回退继续受合法转移与配置化有限步骤上限保护；只有通过 Gate 才能生成 ReadyContent 并结束 Session。

## 9. 用户数据隔离原则

资源归属链固定为：

```text
account → workspace → project → director session
```

第一版一个账号拥有一个默认 Workspace，不设计邀请、角色和多人协作，但底层从一开始保留 `workspace_id` 隔离边界。Session 必须属于 Project，Project 必须属于 Workspace。每次访问都先在上层完成账号到 Workspace、Workspace 到 Project、Project 到 Session 的授权约束，不能只凭 `session_id` 查询或写入。

跨 Workspace 猜测 Project、Session、Turn 或 ReadyContent ID 时，对外不得泄露目标是否真实存在，应表现为当前授权范围内不可见。Director Core 本身不实现账号系统，只接受上层传入的、已经验证且不可由客户端伪造的授权上下文，并在所有存取中强制带入该上下文的 Workspace 与 Project 范围。

## 10. 未来扩展边界

该底座可以在不推翻核心架构的前提下接入未来能力。Memory、历史内容和 Knowledge Retrieval 作为按需上下文来源，经 Context Assembly 提供必要片段，但不得成为固定工作流节点，也不得直接改写 Working State；Knowledge 仍只能辅助判断，不能证明 Owner Facts。

图片、视频、菜单、评论等材料可通过明确材料 Adapter 提供可追溯的候选 Evidence Reference。Phase 1 不启用它们作为 Owner Fact 证据；未来只有 owner-confirmed material 或 trusted structured source 在另行确认后才能进入该证据边界。Production Adapter 只消费 ReadyContent 并连接现有素材、时间轴、FFmpeg 与导出能力，不反向控制 Director 状态机。多模型 Provider 通过统一阶段处理接口替换调用实现，不能改变运行控制、阶段转移、根因诊断和 Readiness Gate 的架构分离。

未来子系统可以拥有自己的存储与契约，但通过稳定边界向 Director 提供上下文或消费结果；不应把六张核心表扩张成通用 Memory、向量库、素材库或任务调度平台。

## 11. 当前架构风险和仍需后续设计的内容

下一阶段仍需明确六表的最小字段、外键与版本约束，SQLite 文件与迁移管理方式，Session 锁在单进程和未来多实例下的实现边界，事务并发策略，规范化请求与幂等唯一性，Checkpoint 生成时机和保留策略，以及 Working State 各类别的最小结构。

还需设计阶段处理器如何分别表达运行控制、目标 Stage、转移原因和根因诊断，Content Readiness Gate 的可验证结果契约，模型失败与超时边界，上下文长度预算，最近完整回合的选择规则，Evidence Reference 的最小形式，ReadyContent 最小 Schema，以及授权上下文如何由现有 API 层传入。Turn 的确定性恢复载荷应在完整 post-state snapshot、版本化 state patch、snapshot + patch 三种形式间权衡确认；不能退化为只存版本号。内部步骤上限的配置位置与默认值也留到执行契约和模拟测试阶段确认。

主要风险是：模型延迟期间 Session 锁会串行化同一任务；步骤间若不以最新候选状态重组上下文，会让 CREATE 或 REVIEW 使用过期输入；Turn 恢复信息不足会迫使系统重新调用模型；Checkpoint 若进入权威事务、越界或重复 Working State，会放大故障和上下文冲突；事实证据关联不严格会让 AI 推断污染 Owner Facts；Gate 过松会草率 READY，过度规则化又会退化为固定评分。这些风险应在 Schema 和执行契约阶段用最小约束解决，而不是引入通用 Event、队列、多 Agent 或复杂任务系统。

## 12. 是否可以进入第二阶段 Schema 设计

**上述架构修正完成并审核通过后，才能进入第二阶段 Schema 设计。**

本文件已经纳入逐步骤上下文重组、Checkpoint 与权威事务分离、Turn 确定性恢复、运行控制与阶段转移分离、可扩展 Evidence Reference、配置化步骤上限和 Raw Transcript 成功提交语义。下一步应先审核这些架构不变量；通过后，第二阶段再把已确认职责落实为最小字段、关系和约束，并继续避免完整 API、复杂索引、通用事件模型和未来能力的提前实现。

```text
是否修改代码：否
是否修改仓库文件：否
是否 commit：否
是否 push：否
```
