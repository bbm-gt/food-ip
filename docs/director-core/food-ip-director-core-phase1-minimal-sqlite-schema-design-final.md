# Director Core Phase 1 最小 SQLite Schema 设计（定稿）

> 依据：已审核通过的 `food-ip-director-core-phase1-core-architecture-final.md`  
> 文档性质：已审核并确认的 Phase 1 Schema 定稿；可作为下一阶段执行契约设计输入  
> 范围：仅六张固定核心表；不包含可执行 SQL、迁移、ORM、API 或未来子系统

## 1. Schema 设计结论

Phase 1 可以由以下六张表完整承载，不需要增加 Fact、Constraint、Event、Pending Turn、Outbox、Lease、任务、Memory、Knowledge、素材库或 Agent Runtime 表：

```text
director_sessions
director_messages
director_working_state
director_turns
director_context_checkpoints
director_ready_content
```

推荐的最小落法是：

1. `director_sessions` 只保存任务归属、来源关系和 `ACTIVE → READY` 终态生命周期。
2. `director_messages` 每个成功 Turn 固定提交一条老板消息和一条整理后的 AI 编导最终可见回复；消息追加且不可改写。
3. `director_working_state` 只保留关系型头部字段与一个版本化结构 JSON，是当前状态唯一权威，不复制完整对话。
4. `director_turns` 保存成功轮次、幂等请求身份、内部步骤记录、首次响应和完整 post-state snapshot。
5. `director_context_checkpoints` 是独立事务写入、可校验和可废弃的连续历史前缀压缩，不参与权威提交。
6. `director_ready_content` 与完成 Session 严格一对一；创建后不可变，后续修改只能建立引用它的新 Session。

Phase 1 确认采用完整 post-state snapshot，而不是 patch 或混合方案。Working State 必须保持紧凑；用少量重复存储换取无模型恢复、低实现复杂度和直观审计，是当前阶段的正式取舍。

## 2. 全局不变量

### 2.1 任务与生命周期

- 一个 Session 只属于一个 `workspace_id + project_id`，只服务一个具体创作任务。
- Session 创建后，`workspace_id`、`project_id`、`source_ready_content_id` 和 `created_at` 不可修改。
- 生命周期最小集合确定为 `ACTIVE`、`READY`；只允许 `ACTIVE → READY`，不允许反向或重复转移。
- `ACTIVE` Session 不得拥有 ReadyContent；`READY` Session 必须且只能拥有一个 ReadyContent。
- ReadyContent 创建是 Session 进入 READY 的唯一入口；READY 后不得再提交 Turn、Message 或 Working State 新版本。
- 后续修改必须创建新 Session；新 Session 可以通过 `source_ready_content_id` 引用旧 ReadyContent，不覆盖原记录。
- 来源 ReadyContent 必须已经存在、不可属于当前 Session、必须来自已 READY Session，并与新 Session 位于同一 Workspace 和 Project。
- `source_ready_content_id` 只允许在 Session 创建时设置。来源必须预先存在且来源字段不可变，因此来源链天然只能指向过去；再配合“生产 Session 不能等于当前 Session”校验，可阻止自引用和环。

### 2.2 已确认契约 A：成功 Turn 的可见消息与原子性

- Working State 初始版本为 `0`；每个成功 Turn 恰好把版本从 `N` 推进到 `N+1`。
- Phase 1 一个成功 Turn 恰好拥有一条 OWNER 消息和一条整理后的 DIRECTOR 最终可见回复。内部可以经过多个阶段和模型调用，但不把内部步骤拆成多条 Raw Transcript 消息。
- 一个成功 Turn 的两条消息、Working State 新版本、Turn 记录、可选 ReadyContent，以及必要的 Session READY 转移，必须在同一个短 SQLite 权威事务中提交。
- 模型失败、校验失败、数据库事务失败、客户端 pending／failed 消息、技术日志和错误诊断都不属于成功 Turn，也不进入 Raw Transcript 或成功 Turns。
- 这是 Phase 1 的交互契约；Schema 只约束本阶段每 Turn 两种角色各一条，不宣称未来产品永久不能通过新版本扩展可见消息模型。
- 模型调用、阶段循环和完整校验发生在权威事务之前；其中任何一步失败都不写入这四类权威数据。
- Checkpoint 不属于上述事务，只能在权威事务成功后以独立 best-effort 短事务写入。

### 2.3 已确认契约 B：修改 ReadyContent 时的最小继承

新 Session 基于 `source_ready_content_id` 修改旧 ReadyContent 时，只读取**直接来源 ReadyContent 对应 Session 的最终 Working State**，自动复制其中：

- 最终仍有效的 Owner Facts；
- 最终仍有效的 Owner Constraints；
- 老板已认可的当前 `direction`；
- 原 ReadyContent，作为本次修改的来源内容。

不得自动继承完整 Raw Transcript、Rejected Items、已纠正事实、Unconfirmed Inferences、普通 AI Judgments、已放弃方向，也不得递归扫描祖先 Session 或从直接来源／祖先 Transcript 任意挑选消息。

继承对象保留其已有 Evidence Reference，并增加直接继承来源标记。最小推荐字段为：

```json
{
  "inherited_from": {
    "source_ready_content_id": "<直接来源 ReadyContent ID>",
    "source_session_id": "<直接来源 Session ID>"
  }
}
```

该标记说明“本对象由哪个直接来源最终状态带入”，不替代原始 Evidence Reference，也不把 ReadyContent 当作 Owner Fact 原始证据。若直接来源最终有效对象本身是更早继承而来，它可以保留原始消息证据，但当前 Session 仍只复制直接来源的最终对象，不读取或扫描祖先状态／Transcript。

原 ReadyContent 通过 Session 的 `source_ready_content_id` 保持不可变引用，并作为新 Session 的修改基线。最小推荐是在初始化 `draft` 时复制其 `final_content_json`，同时在 draft 投影记录 `based_on_ready_content_id = source_ready_content_id`；这不是把 ReadyContent 当 Evidence，也不允许覆盖来源记录。

老板新消息与继承事实冲突时，新消息优先；旧事实进入当前 Session 的 Rejected Items。时间敏感事实或任务语境明显变化时，该对象不得进入活跃 `owner_facts` 或 `direction`；最小做法是在 `material_state.required_confirmations` 中保存待确认对象的必要投影、原 Evidence Reference、`inherited_from` 和原因，直至新的 OWNER Message 完成确认。这不是把旧事实改写成 Unconfirmed Inference。

### 2.4 权威边界

- Raw Transcript 只回答“老板和 AI 实际说过什么”。
- Working State 只回答“当前任务应当相信和使用什么”。
- Turn 只回答“这一轮如何完成、如何幂等回放、如何恢复其 post-state”。
- Checkpoint 只回答“较早的连续对话前缀发生过什么”，不能成为 Owner Fact 证据。
- ReadyContent 只回答“最终交付内容是什么”，不能被后续编辑覆盖。
- 任何 JSON 都不得保存模型隐藏推理、隐藏提示词、内部思维链或未提交请求。

### 2.5 外键与删除

- 每个 SQLite 连接必须显式启用外键检查；若启动自检发现外键未启用，Director Core 不得接受写请求。
- 六表间外键全部使用限制删除语义，不做级联删除。Phase 1 不设计 Session 或历史记录删除流程。
- `workspace_id` 和 `project_id` 当前是上层授权与归属标识。现有仓库没有可供这六表引用的 Workspace／Project SQLite 权威表，且本任务禁止新增表，因此它们只能设为非空、创建后不可变，并由上层授权上下文校验存在性及归属。不能虚构一个并不存在的数据库外键。

## 3. 六张表逐表设计

以下是逻辑字段定义，不是可执行 SQL。

### 3.1 `director_sessions`

| 字段 | 推荐类型 | 空值 | 约束与含义 |
| --- | --- | --- | --- |
| `id` | TEXT | 否 | 主键；规范化 UUIDv4 字符串 |
| `workspace_id` | TEXT | 否 | 上层已授权 Workspace 标识；创建后不可变 |
| `project_id` | TEXT | 否 | 上层已授权 Project 标识；创建后不可变 |
| `source_ready_content_id` | TEXT | 是 | 外键到 `director_ready_content.id`，限制删除；只在创建时设置 |
| `lifecycle_status` | TEXT | 否 | 仅 `ACTIVE` 或 `READY` |
| `created_at` | TEXT | 否 | `YYYY-MM-DDTHH:MM:SS.sssZ` 格式的 UTC 固定毫秒时间；创建后不可变 |
| `ready_at` | TEXT | 是 | ACTIVE 时必须为空；READY 时必须非空且等于 ReadyContent 创建时间 |

约束：

- `lifecycle_status = ACTIVE` 当且仅当 `ready_at IS NULL`；`READY` 当且仅当 `ready_at IS NOT NULL`。
- 来源外键使用立即检查，不能使用延迟外键：新 Session 只能引用已经提交的旧 ReadyContent。
- 创建 Session 时的跨表校验必须确认来源 ReadyContent 的生产 Session：不是当前 Session、已经 READY、`workspace_id` 和 `project_id` 与新 Session 完全相同。
- Session 关系字段全部不可更新；生命周期只允许由 ReadyContent 创建流程触发一次 `ACTIVE → READY`。
- 不在 Session 冗余保存 `ready_content_id`。ReadyContent 侧的唯一 `session_id` 已能表达严格一对一，避免双向字段不一致。
- 有来源的 Session 初始化 Working State 时，只从直接来源 Session 的最终有效状态复制契约 B 允许的对象，并给每个继承对象写入 `inherited_from`。初始化过程不读取来源 Transcript，也不递归读取来源 Session 的来源。

Session 创建事务同时创建唯一 Working State，统一使用 `state_version = 0`、`stage = EXPLORE`、`latest_successful_turn_id = NULL`，但初始 `state_json` 分为两种：

- **普通新创作 Session**：使用空的版本 1 状态结构，`owner_facts`、`owner_constraints` 为空，`direction = null`，`draft = null`。
- **基于 ReadyContent 的修改 Session**：从直接来源 Session 的最终有效状态复制契约 B 允许的 Owner Facts、Owner Constraints 和已认可 `direction`，并以原 ReadyContent 初始化 `draft`；不得读取完整 Transcript 或递归扫描祖先 Session。

两类 Session 都从 `EXPLORE` 开始。修改 Session 的第一轮由 Orchestrator 判断原方向是否继续有效；有效时可在同一 Turn 内自动推进，失效时留在 EXPLORE 重新确认方向。

### 3.2 `director_messages`

| 字段 | 推荐类型 | 空值 | 约束与含义 |
| --- | --- | --- | --- |
| `id` | TEXT | 否 | 主键；规范化 UUIDv4 字符串 |
| `session_id` | TEXT | 否 | 外键到 `director_sessions.id`，限制删除 |
| `message_seq` | INTEGER | 否 | 正整数；Session 内稳定且唯一 |
| `visible_role` | TEXT | 否 | 仅 `OWNER`、`DIRECTOR` |
| `content` | TEXT | 否 | 原样保存可见消息内容；不可为空 |
| `turn_id` | TEXT | 否 | 与 `session_id` 组成复合外键到同 Session 的 Turn |
| `created_at` | TEXT | 否 | 成功权威事务提交所使用的 UTC 时间 |

唯一约束：

- `UNIQUE(session_id, message_seq)`：Session 内消息顺序不可冲突。
- `UNIQUE(session_id, turn_id, visible_role)`：每个 Turn 每种可见角色至多一条。
- 为复合引用提供 `UNIQUE(session_id, id)` 候选键。

Phase 1 明确每个成功 Turn 只有两条服务端 Raw Transcript 消息。推荐把消息顺序与 Turn 的 post-state version 对齐：

```text
OWNER    message_seq = 2 × post_state_version - 1
DIRECTOR message_seq = 2 × post_state_version
```

这样初始版本 0 后，第一个成功 Turn 产生序号 1、2，第二个产生 3、4；序号天然连续、稳定且完整回合末尾总是偶数。该映射由跨表校验／最小触发器检查，不能只依赖调用方自觉。

Turn 不反向保存 OWNER／DIRECTOR Message ID，避免 Turn 与 Messages 形成循环外键。`director_messages.turn_id` 使用到同 Session Turn 的复合外键；`UNIQUE(session_id, turn_id, visible_role)` 加上角色枚举保证每种角色至多一条。提交前闭合校验必须确认该 Turn 恰好有一条 OWNER 和一条 DIRECTOR；Working State CAS 更新也只能引用已经满足这项完整性检查的 Turn。数据库约束与事务内最终校验共同保证不能提交半个可见回合。

追加语义：

- 禁止 UPDATE 和 DELETE；纠正只能追加新的 OWNER 消息并更新下一版 Working State。
- 插入时 Session 必须仍为 ACTIVE。
- 不保存系统角色、工具调用、内部阶段消息、模型隐藏推理或失败请求。

### 3.3 `director_working_state`

| 字段 | 推荐类型 | 空值 | 约束与含义 |
| --- | --- | --- | --- |
| `session_id` | TEXT | 否 | 主键，同时是到 Session 的外键；形成一对一 |
| `state_version` | INTEGER | 否 | 非负；初始 0；每次成功 Turn 严格 +1 |
| `stage` | TEXT | 否 | `EXPLORE`、`DEEPEN`、`CREATE`、`REVIEW`、`READY` |
| `state_json` | TEXT | 否 | 当前紧凑结构状态；合法 JSON 对象，内部含 `format_version` |
| `state_sha256` | TEXT | 否 | `canonical_state_envelope` 规范化字节的 SHA-256 小写十六进制值 |
| `latest_successful_turn_id` | TEXT | 是 | 版本 0 时为空；之后指向同 Session、产生当前版本的 Turn |
| `updated_at` | TEXT | 否 | 初始为 Session 创建时间；成功 Turn 后更新为提交时间 |

普通字段只保留必须用于关系、并发条件、状态机和快速完整性检查的内容：Session、版本、Stage、最新成功 Turn、哈希、更新时间。Owner Facts 等内容采用一个 `state_json`，原因是它们构成同一个原子状态文档，拆成多个 JSON 列会制造跨列版本和校验不一致，也不会获得真正的关系型收益。

`state_json` 不保存 `session_id`、`state_version` 或 `stage` 的副本；这些普通列是当前行权威。Turn 的完整 snapshot 则把这些普通列与 `state_json` 一起封装，确保可独立恢复。

版本与修改保护：

- 正常更新必须使用 `WHERE session_id = ? AND state_version = ?` 的乐观条件，且新版本严格等于旧版本加一；受影响行数不是 1 即整轮失败。
- 正常更新时 Session 必须为 ACTIVE，`latest_successful_turn_id` 必须改为已经插入的本轮 Turn ID；该外键是单向的 Working State → Turn，不需要延迟或循环外键。
- Working State CAS 更新前，事务内校验本轮 Turn 已存在、属于同一 Session，且恰好存在一条 OWNER 和一条 DIRECTOR Message。更新后 Turn 的 post version、target Stage、post-state hash 必须与当前 Working State 完全一致。
- READY 后禁止任何产生新版本的更新。确定性修复不是新创作：如需从 Turn snapshot 恢复损坏投影，只允许受控维护路径写回同一版本、Stage、latest Turn 和经重新计算一致的哈希，不能借恢复名义推进版本。
- 哈希定义唯一且固定为：

```text
canonical_state_envelope =
{
  state_version,
  stage,
  state_json
}
```

`state_sha256` 对该信封的规范化 UTF-8 字节计算。SQLite 本身不负责 SHA-256；仓储层在写入和读取时重算。Turn 的 `post_state_sha256` 对完全相同的信封计算，两者必须一致。

完整对话不会复制进 Working State。JSON 只保存当前有效事实、判断、推断、拒绝项、约束，以及方向、素材、草稿、评审的必要投影；原文通过 Evidence Reference 定点回读 `director_messages`。

### 3.4 `director_turns`

| 字段 | 推荐类型 | 空值 | 约束与含义 |
| --- | --- | --- | --- |
| `id` | TEXT | 否 | 主键；规范化 UUIDv4 字符串 |
| `session_id` | TEXT | 否 | 外键到 Session；限制删除 |
| `client_message_id` | TEXT | 否 | 客户端生成的稳定幂等键；非空 |
| `request_format_version` | INTEGER | 否 | 正整数；规范化请求结构版本 |
| `normalized_request_json` | TEXT | 否 | 影响本轮行为的规范化请求信封；合法 JSON 对象 |
| `request_sha256` | TEXT | 否 | 上述规范化 UTF-8 字节的 SHA-256 |
| `pre_state_version` | INTEGER | 否 | 非负 |
| `post_state_version` | INTEGER | 否 | 必须等于 `pre_state_version + 1` |
| `final_run_control` | TEXT | 否 | 成功 API Turn 终点只允许 `WAIT_FOR_OWNER` 或 `READY` |
| `target_stage` | TEXT | 否 | 本轮结束后的五阶段之一，须等于 Working State Stage |
| `transition_reason_code` | TEXT | 否 | 稳定、可审计的原因码；不存隐藏推理 |
| `gate_outcome` | TEXT | 是 | `PASSED`、`BLOCKED` 或空；作为本轮终点必要投影 |
| `review_root_cause` | TEXT | 是 | `WRITING_PROBLEM`、`MATERIAL_PROBLEM`、`DIRECTION_PROBLEM` 或空 |
| `execution_format_version` | INTEGER | 否 | 内部步骤结构版本 |
| `execution_trace_json` | TEXT | 否 | 本轮有限内部步骤的结构化结果；合法 JSON 对象 |
| `response_format_version` | INTEGER | 否 | 首次 API 成功响应结构版本 |
| `first_response_json` | TEXT | 否 | 首次成功响应快照，用于幂等回放 |
| `snapshot_format_version` | INTEGER | 否 | post-state snapshot 信封版本 |
| `post_state_snapshot_json` | TEXT | 否 | 本轮提交后完整 Working State snapshot |
| `post_state_sha256` | TEXT | 否 | 与 Working State 完全相同的 `canonical_state_envelope` SHA-256 |
| `created_at` | TEXT | 否 | 成功权威事务提交时间 |

唯一约束：

- `UNIQUE(session_id, client_message_id)`：幂等边界。
- `UNIQUE(session_id, post_state_version)`：每个 Session 的每个成功状态版本只有一个生产 Turn。
- 为子表复合外键提供 `UNIQUE(session_id, id)`。

幂等请求身份由三部分组成：`request_format_version`、规范化请求 JSON、SHA-256。相同 `client_message_id` 命中时：

- 版本、哈希且规范化 JSON 字节均相同：直接返回 `first_response_json`，不调用模型，不再次写表；
- 任一不同：识别为幂等键冲突；
- 先比哈希、再比规范化字节，避免只把哈希碰撞假设当作数据相等。

规范化请求只包含老板可见输入和确实影响处理的显式请求参数，并把默认值展开；不把当前 Working State、授权令牌或服务器瞬时时间混入。换行、Unicode 和数字等规范化规则必须由后续执行契约固定并由 `request_format_version` 管理，不能在实现中临时变化。

`execution_trace_json` 的最小结构是一组按序内部步骤，每步包含：`step_no`、`entered_stage`、运行控制、目标 Stage、转移原因码，以及可选的 Content Readiness Gate 结果或 REVIEW 根因。它不保存提示词、模型隐藏推理或自由散漫的内部日志。数据库普通列保存本轮最终投影，JSON 保存本轮可能经历的多个内部阶段。

READY 约束：`final_run_control = READY` 时，`target_stage` 必须为 READY、Gate 必须通过、Working State 必须为 READY，并且 execution trace 的最后一次 REVIEW 结果必须为通过；否则 ReadyContent 插入被拒绝。非 READY Turn 的最终控制只能是 `WAIT_FOR_OWNER`。

`post_state_snapshot_json` 推荐包含：snapshot 格式版本、`state_version`、`stage`、完整 `state_json`。其中可有 snapshot 外层格式信息，但 `post_state_sha256` 只计算内部完全相同的 `canonical_state_envelope = {state_version, stage, state_json}`；`snapshot_format_version` 不参与该哈希。Phase 1 不额外增加 snapshot 包装层哈希。恢复时先校验 snapshot 格式，再从其中提取 canonical state envelope，重算并比对 `post_state_sha256` 和 Working State `state_sha256`，校验 Evidence Reference 后直接恢复；不得重新调用模型。

### 3.5 `director_context_checkpoints`

| 字段 | 推荐类型 | 空值 | 约束与含义 |
| --- | --- | --- | --- |
| `id` | TEXT | 否 | 主键；规范化 UUIDv4 字符串 |
| `session_id` | TEXT | 否 | 外键到 Session；限制删除 |
| `covered_through_seq` | INTEGER | 否 | 正整数；完整覆盖的 Raw Transcript 连续前缀末序号 |
| `format_version` | INTEGER | 否 | 正整数；Checkpoint 内容格式版本 |
| `checkpoint_json` | TEXT | 否 | 确定性结构化压缩；合法 JSON 对象 |
| `integrity_sha256` | TEXT | 否 | Checkpoint 规范化信封的 SHA-256 |
| `status` | TEXT | 否 | `VALID` 或 `DISCARDED` |
| `discarded_at` | TEXT | 是 | VALID 时为空；DISCARDED 时非空 |
| `discard_reason_code` | TEXT | 是 | 废弃原因码；VALID 时为空 |
| `created_at` | TEXT | 否 | 独立事务创建时间 |

约束：

- `(session_id, covered_through_seq)` 复合外键指向同 Session 的现存消息序号。
- 创建时必须确认 Session 消息序号 `1..covered_through_seq` 无缺口，且边界序号对应一条 `visible_role = DIRECTOR` 的 Message；该 Message 的 `turn_id` 在同一 Session 下还必须恰好关联一条 OWNER 和一条 DIRECTOR。结合每 Turn 两条消息的序号规则，边界只能落在完整成功回合末尾，不依赖 Turn 反向消息 ID。
- Checkpoint 可以覆盖 ACTIVE 或 READY Session 已提交的历史，但不能越过当时最新已提交消息。
- `integrity_sha256` 对 `{format_version, session_id, covered_through_seq, checkpoint_json}` 的规范化信封计算。
- 内容、边界、格式、哈希和创建时间不可更新；只允许 `VALID → DISCARDED`，不能恢复为 VALID。重建必须插入新记录。
- 可选唯一约束 `UNIQUE(session_id, covered_through_seq, format_version, integrity_sha256)` 用于避免完全相同的重复缓存，但不禁止同一边界存在不同重建结果。

多个 Checkpoint 共存时，不设置会产生竞争的 `is_latest` 标志。上下文组装器只考虑受支持格式、状态为 VALID、哈希和边界验证通过的记录，按 `covered_through_seq DESC, created_at DESC, id DESC` 选择第一条。旧 Checkpoint 可以继续保持 VALID；“最新”是确定性查询结果，不是额外权威状态。

`checkpoint_json` 最小只需要表达过去的结构化对话进展，例如 `dialogue_progress`、`owner_choices`、`abandoned_branches`，项目均可引用消息 ID。它不重复当前 Owner Facts、完整草稿或完整 Transcript，也不能作为 Evidence Reference。

### 3.6 `director_ready_content`

| 字段 | 推荐类型 | 空值 | 约束与含义 |
| --- | --- | --- | --- |
| `id` | TEXT | 否 | 主键；规范化 UUIDv4 字符串 |
| `session_id` | TEXT | 否 | 外键到 Session；全表唯一，形成严格一对一 |
| `content_format_version` | INTEGER | 否 | 正整数；最终内容结构版本 |
| `final_content_json` | TEXT | 否 | 不可变最终产物；合法 JSON 对象 |
| `created_by_turn_id` | TEXT | 否 | 与 Session 组成复合外键到生产它的成功 Turn |
| `created_at` | TEXT | 否 | READY 成功事务提交时间 |

唯一约束：

- `UNIQUE(session_id)`：一个 Session 最多一个 ReadyContent。
- `UNIQUE(created_by_turn_id)`：一个成功 Turn 最多生成一个 ReadyContent。

创建前的跨表约束必须同时成立：Session 仍为 ACTIVE；生产 Turn 属于同一 Session；该 Turn 是 Working State 的 `latest_successful_turn_id`；Turn 的 post version 与 Working State 当前版本相同；Turn 最终控制和目标 Stage 均为 READY；REVIEW 已通过；Working State Stage 为 READY。

推荐以最小触发器／等价数据库保护在 ReadyContent 成功插入后立即把 Session 改为 READY，并令 `ready_at = ReadyContent.created_at`；Session 的直接 READY 更新只有在对应 ReadyContent 已存在且时间一致时才允许。这样最终提交状态始终是“0/1 ReadyContent 与生命周期一致”，而不是依赖两个互不校验的应用写操作。

ReadyContent 禁止 UPDATE 和 DELETE。后续修改创建新 Session，并在新 Session 的 `source_ready_content_id` 中引用该记录。

## 4. 表关系说明

```text
director_sessions
  ├─ 1 : 1 ─ director_working_state
  ├─ 1 : N ─ director_turns
  │            └─ 1 : 2 ─ director_messages（由 Message.turn_id 单向关联）
  ├─ 1 : N ─ director_context_checkpoints
  └─ ACTIVE 时 1 : 0；READY 时 1 : 1 ─ director_ready_content

director_sessions.source_ready_content_id
  └─ 0 : 1 ─ 先前 Session 的 director_ready_content.id
```

Turn 与 Messages 只保留单向关系：`director_messages.turn_id → director_turns.id`，并同时校验 Session 一致。Turn 不保存消息 ID。每 Turn 两种角色各至多一条由唯一约束保证，“恰好各一条”由 Working State CAS 前检查和提交前闭合校验保证；任一失败使整个权威事务回滚。

Working State 与最新 Turn 也有双向一致性：Working State 指向当前生产 Turn，Turn 保存 post-state version、snapshot 和 hash。前者提供当前权威入口，后者提供历史恢复依据。

## 5. Working State 最小结构

### 5.1 已确认顶层 JSON

```json
{
  "format_version": 1,
  "owner_facts": [],
  "ai_judgments": [],
  "unconfirmed_inferences": [],
  "rejected_items": [],
  "owner_constraints": [],
  "direction": null,
  "material_state": {},
  "draft": null,
  "review": null
}
```

含义边界：

- `owner_facts`：当前有效且至少有一条合法老板消息证据的事实。
- `ai_judgments`：候选方向、判断理由、结构和表达建议；不能自动成为事实，也不能保存一个可独立修改的“当前方向”权威副本。候选方向被提升后可以保留历史关系，例如指向对应 `direction.item_id`，但不得继续带有独立 `is_current` 语义。
- `unconfirmed_inferences`：尚未得到老板确认的推断；需要确认后才可进入 Owner Facts。
- `rejected_items`：已否定、已纠正或不再采用的事实、推断、判断或方向；保留来源与失效关系以防重新混入。
- `owner_constraints`：老板明确提出的表达、拍摄、禁用或边界要求；应有老板消息证据。
- `direction`：当前活跃方向的唯一权威投影。Phase 1 推荐在老板认可前保持为 `null`，候选方向只放在 `ai_judgments`；一旦老板认可，`direction` 必须包含稳定 `item_id`、方向内容、明确的老板认可状态和至少一条 OWNER Message Evidence Reference。方向被替换或否定后，旧方向进入 Rejected Items 或明确失效，不得同时在 `direction` 与 `ai_judgments` 中存在两个可独立修改的当前状态。
- `material_state`：当前表达所需真实素材的满足／缺口投影，不是素材库。
- `draft`：当前唯一工作草稿；不是多候选 Bundle。
- `review`：最近有效评审的根因、结论及其针对的 draft 标识；不是固定评分表。

每个数组项都应有稳定 `item_id` 和类型所需的最小内容。只有会成为事实或老板确认边界的对象携带 Evidence Reference；不要给所有 AI 判断强行附证据。继承的 Owner Fact、Owner Constraint 和 `direction` 还带可选 `inherited_from`，只记录直接来源 ReadyContent／Session。

### 5.2 JSON 版本规则

- `state_json` 顶层必须有 `format_version`，从 1 开始；子对象不各自增加版本号，避免 Phase 1 版本爆炸。
- 表中已有独立 `*_format_version` 的其他 JSON，不再在内容内部重复同一版本；snapshot 自包含时例外。
- 同一格式版本内只允许向后兼容的解释修正；字段改名、含义改变、必填性改变必须提升格式版本并提供确定性升级函数。
- 未支持的版本不得被模型“猜着读”；应拒绝写入或触发受控恢复／升级。

### 5.3 不复制完整对话

- 禁止 `messages`、`transcript`、`conversation_history` 等完整原文数组出现在 Working State。
- Fact 中保存简洁、规范化的当前事实表达和 Evidence Reference，而不是复制整条老板消息。
- 当前 draft 可以保存完整草稿，因为它本身就是当前创作状态；历史草稿由 Turn snapshots 提供，不在当前 state 中累积。
- 需要原文时，根据 Evidence Reference 定点读取 Messages；需要较早过程时使用 Checkpoint；两者都不改变 Working State 权威。

## 6. Turn 确定性恢复方案比较

| 方案 | 实现复杂度 | 恢复可靠性 | 数据量 | Schema 稳定性 | 调试与审计 | Phase 1 评价 |
| --- | --- | --- | --- | --- | --- | --- |
| A. 每个成功 Turn 保存完整 post-state snapshot | 低 | 最高；单 Turn 即可恢复 | 最高，但 Working State 被要求保持紧凑 | 高；snapshot 自带格式版本 | 最直观，可直接比对任意版本 | 推荐 |
| B. 每个成功 Turn 保存 versioned state patch | 高 | 依赖从基线开始的完整补丁链；一处损坏影响后续 | 最低 | 较低；数组、删除、重命名和语义变更都要求稳定 patch 规范 | 需要重放才能看到状态，定位较难 | 不推荐 Phase 1 |
| C. snapshot + patch 混合 | 中高 | 高，但依赖 snapshot 周期和 patch 链管理 | 中 | 中；同时维护两套格式与升级规则 | 较好，但审计路径更复杂 | 暂不值得 |

Phase 1 确认采用 A：每个成功 Turn 保存完整 post-state snapshot。

理由：

- 架构要求任何成功 Turn 都能无需模型确定性恢复；A 直接满足，不依赖前序链。
- Working State 已被限制为紧凑投影，重复存储规模在 Phase 1 可接受。
- Fact、Rejected Item、Evidence 数组的 patch 语义很容易因排序、纠正、移动和格式升级变脆。
- A 只需一种 snapshot 格式和一种校验路径，最利于模拟测试、人工审计和故障定位。

替代方案：若未来经真实数据证明 snapshots 成为明确容量或 I/O 瓶颈，再以测量结果讨论 C；不能在 Phase 1 预先引入 snapshot 周期、patch 合并和保留算法。B 不应作为默认演进方向。

该决定已确认，作为 Phase 1 的正式持久化契约。

## 7. Evidence Reference 设计

### 7.1 放置位置

Evidence Reference 确认放在 `state_json` 内对应的 Owner Fact／Owner Constraint／已认可方向对象中，不新增表，也不塞入 Message 或 Turn 的通用字段。

理由：

- 六表范围内只有 Working State 承担当前事实语义；证据关系随事实的当前有效性一起原子版本化。
- 一个 Fact 对多条证据、一次纠正影响多个旧项，本质是 Working State 内部结构；不增加第七张关联表也可正确表达。
- 每个 Turn 的完整 snapshot 会连同 Evidence Reference 保存历史版本，恢复时不丢关联。

代价是 JSON 内引用不能获得普通 SQL 外键。Phase 1 必须由权威事务的结构校验器逐条解析并查询 Messages 做归属校验；这是选择六表方案后的明确边界，而不是假装 SQLite 能给 JSON 数组建立外键。

### 7.2 最小结构

```json
{
  "evidence_type": "owner_message",
  "target_id": "<message UUID>",
  "target_session_id": "<message 所属 Session UUID>"
}
```

Phase 1 写入校验只接受 `owner_message`。保留 `evidence_type` 判别字段，是为了未来通过新格式版本增加类型，而不是现在实现未来来源。

Evidence 在本轮处理时分为两种提交状态：

```text
已提交 Evidence：已经存在于成功 Raw Transcript 的 OWNER Message
本轮候选 Evidence：ID 已预生成、将在当前权威事务中与新状态一起插入的 OWNER Message
```

当前 OWNER Message ID 在进入模型处理前预生成，当前老板输入作为内存候选消息参与上下文组装。因此候选 Working State 可以引用这个尚未提交的 ID，不能要求它在事务开始前已存在于 Raw Transcript。事务前应用校验引用内容确实来自本轮老板输入；事务内先把该 OWNER Message 插入，再做 Working State CAS 和最终 Evidence 闭合校验。事务失败时，候选 Evidence、Turn、两条 Messages、Working State 和可选 ReadyContent 一起回滚。

每条非继承 Evidence Reference 必须验证：

1. `target_id` 对应的 Message 存在；
2. Message 的 `visible_role` 为 OWNER；
3. Message 的实际 `session_id` 等于 `target_session_id`；
4. `target_session_id` 是当前 Session；本轮候选引用在事务最终闭合检查时必须已经插入；
5. 被引用 Message 属于当前 Session 的成功 Turn；
6. 同一对象内重复引用去重。

继承对象采用不同的合法性证明：当前 Session 只能从直接来源 Session 的最终 `state_json` 复制契约 B 允许的有效对象，完整保留该对象已有的 Evidence Reference，并增加 `inherited_from`。校验器必须逐对象比对直接来源最终状态，确认对象、证据引用和来源标记没有被扩张或替换。它不遍历祖先状态或 Transcript，也不允许从直接来源／祖先 Session 任意新增消息引用。若直接来源对象原本已继承自更早 Session，其原始 Evidence Reference 可以原样继续存在，因为当前 Session 复制的是直接来源最终有效对象，而不是重新扫描祖先消息。

ReadyContent 及 `inherited_from` 只证明继承路径，不是 Owner Fact 的原始证据。任意同 Workspace 的无关 Session 消息不得引用。

### 7.3 一个 Fact 的最小形式

```json
{
  "item_id": "<UUID>",
  "statement": "<当前有效事实的简洁表达>",
  "evidence_refs": [
    {
      "evidence_type": "owner_message",
      "target_id": "<message UUID>",
      "target_session_id": "<session UUID>"
    }
  ],
  "supersedes_item_ids": [],
  "inherited_from": null
}
```

`evidence_refs` 至少一条，可以多条；证据共同支撑同一 Fact 时全部保留。模型整理后的 `statement` 不得扩大原证据含义。

### 7.4 老板纠正事实

- Raw Transcript 保留新旧 OWNER 消息，不修改旧消息。
- 旧 Fact 从 `owner_facts` 移到 `rejected_items`，保留原 `item_id`、原陈述和原 `evidence_refs`。
- 旧项增加拒绝原因 `OWNER_CORRECTED`、纠正它的 `rejected_by_evidence_refs`，以及可选的 `superseded_by_item_id`。
- 新 Fact 使用新 `item_id`，证据指向纠正消息，并通过 `supersedes_item_ids` 指向旧 Fact。
- 后续上下文只把新 Fact 当作当前事实；旧证据仍可用于解释纠正过程，但不能重新提升旧事实。
- 若被纠正的是继承事实，新 Fact 不沿用旧对象的 `inherited_from`；Rejected Item 保留旧继承标记用于审计，新 Fact 由当前 Session 的新 OWNER Evidence 支撑。

未来的 `owner_confirmed_material`、`trusted_structured_source` 只保留判别字段扩展边界。Phase 1 校验器必须拒绝这两种值；本文不为其设计表、字段或授权逻辑。

## 8. 幂等、版本与事务约束

### 8.1 Session 创建事务

同一短事务中：

1. 校验上层授权的 Workspace／Project 和可选来源 ReadyContent；
2. 插入 ACTIVE Session；
3. 构造版本 0、EXPLORE 的唯一 Working State：普通 Session 使用空状态；修改 Session 按契约 B 复制直接来源最终有效对象并以原 ReadyContent 初始化 `draft`；
4. 对初始 `state_json`、继承对象、Evidence Reference 和 `state_sha256` 完成校验；
5. 插入 Working State；
6. 提交。

没有初始 Turn 或 Message。Session 创建失败不留下孤立状态。

### 8.2 成功 Turn 权威事务

以下只是本 Schema 的最小无循环外键提交次序，用于证明字段和约束可落地；不展开 API、错误码、重试、锁或执行契约。

1. 在进入模型处理前预生成 Turn ID、OWNER Message ID、DIRECTOR Message ID；OWNER ID 可被本轮候选 Evidence Reference 使用。
2. 在内存中完成全部模型调用、内部阶段循环、候选 Working State、Evidence、DIRECTOR 最终可见回复、首次响应和可选 ReadyContent 校验。此时不写权威表。
3. 开启短事务，重新确认 Session 为 ACTIVE，当前 Working State version 等于 expected `N`。
4. 先插入 Turn。Turn 只外键到 Session，不反向引用 Messages；校验 `pre_state_version = N`、`post_state_version = N+1`，保存 snapshot 与统一 canonical state envelope 哈希。
5. 插入一条 OWNER Message 和一条 DIRECTOR Message；两者通过 `(session_id, turn_id)` 复合外键指向刚插入的同 Session Turn，并满足角色唯一和派生序号约束。
6. 事务内校验该 Turn 现在恰好有两条 Message，且 OWNER／DIRECTOR 各一条；同时逐条闭合本轮候选 Evidence，确认目标 Message 已存在、角色为 OWNER、Session 与 Turn 合法。任何缺失立即失败。
7. 以 `session_id + expected version N + ACTIVE` 做 Working State CAS，写入版本 `N+1`、新 Stage、`latest_successful_turn_id = Turn ID` 和新的 `state_sha256`。CAS 前／触发器校验 Turn、两条 Messages、post version、target Stage 和 `post_state_sha256` 与候选状态一致。
8. 若本轮 READY，插入 ReadyContent，并完成 Session 的唯一 `ACTIVE → READY` 转移。
9. 提交前执行最终闭合校验：Turn、两条 Messages、Working State、Evidence、哈希，以及可选 ReadyContent／Session 终态全部一致。任一步失败都回滚整个事务。
10. 权威事务提交成功后，才 best-effort 生成 Checkpoint，并以独立短事务写入。

由于外键方向只有 Messages → Turn、Working State → Turn、ReadyContent → Turn，不存在 Turn → Messages 的反向外键，也不需要为插入顺序引入循环或延迟外键。“Turn 行先插入”只是同一未提交事务中的中间状态；只有第 9 步闭合并提交后才成为成功 Turn。

### 8.3 Checkpoint 独立事务

权威事务成功后，独立读取已提交 Transcript 和 Turns，生成 Checkpoint，再用短事务重新校验覆盖边界并插入。生成、校验、插入或后续废弃失败，都不改变成功 Turn 的 API 结果，也不回滚任何权威数据。

### 8.4 最小数据库保护

建议用 CHECK、UNIQUE、复合／延迟 FOREIGN KEY 和少量只表达不变量的触发器保护：

- Session 来源合法、关系字段不可变、生命周期只进不退；
- Message／Turn／ReadyContent 追加或不可变；
- Working State 正常版本严格 +1，READY 后不能推进；
- Turn 与当前 post-state version、latest Turn、统一 canonical state envelope hash 一致；
- 一个 Turn 在提交时必须有两条正确角色的 Message；Message 单向引用 Turn，不使用反向消息 ID；
- 消息序号和 Turn post version 对齐；
- ReadyContent 只能由同 Session 的最新 REVIEW→READY Turn 创建，并同步终结 Session；
- Checkpoint 边界是完整回合末尾，内容只可废弃不可覆盖。

SQLite 不能仅靠声明式 Schema 深度验证版本化 JSON、计算 SHA-256、判断继承对象是否与直接来源最终状态相同，或判断自然语言是否真的被证据支持。这些由同一仓储／领域校验层在事务写入前和提交前闭合检查中执行；Schema 负责阻止明显错配和并发冲突，不能取代产品语义校验。

## 9. SQLite Phase 1 并发边界

### 9.1 单进程成立条件

- 单进程维护每 Session 的互斥锁，锁覆盖幂等预检、模型调用、内部阶段循环和权威事务，使同一 Session 不交错处理。
- 模型调用期间不持有 SQLite 写事务；只有最终提交使用短事务。
- 不同 Session 可以并行做模型工作；SQLite 最终写入仍按其单写者模型短暂串行。
- 权威事务开始后重新校验 Session ACTIVE 和 Working State expected version，不能相信模型调用前的旧读结果。
- 每连接启用外键，并使用适当的短等待／WAL 配置属于执行配置，不改变 Schema。

### 9.2 Schema 与权威事务闭合检查能阻止的错误提交

- 同一 Session 的重复 `client_message_id`；
- 同一状态版本产生两个成功 Turn；
- Working State 丢版本或跳版本；
- 声明式约束阻止消息序号冲突和同 Turn 角色重复；CAS 前检查及提交前闭合校验阻止半个可见回合；
- Turn、Message、ReadyContent 跨 Session 错绑；
- READY Session 继续提交正常状态版本；
- 一个 Session 多个 ReadyContent、一个 Turn 多个 ReadyContent；
- 非完整回合末尾的 Checkpoint；
- 来源 ReadyContent 自引用、跨 Workspace／Project 或引用未完成 Session。

### 9.3 未来多实例必须重审的部分

进程内 Session 锁不能跨实例。SQLite 的 CAS 和唯一约束仍能保证两个实例不会同时正确提交同一 pre-state，但不能阻止它们在提交前重复调用模型，也不能给不同 `client_message_id` 的并发老板消息定义低成本排队顺序。

未来多实例时必须重新设计跨实例 Session 执行协调、超时所有权、失败恢复和部署拓扑；可能需要数据库级协调或外部协调能力。Phase 1 不提前增加 Lease、Pending Turn、任务队列、Outbox 或分布式锁表。SQLite 单写者也会成为部署容量边界，届时应依据真实负载重新决策，而不是现在引入 PostgreSQL。

## 10. 已确认的重大 Schema 决定

以下八项已经确认，并作为 Director Core Phase 1 的正式持久化契约。后续执行契约、迁移和实现不得自行改变；如需修改，必须重新进行架构与数据兼容性确认。

| # | 决策 | 已确认方案 | 主要原因 | Phase 1 边界 |
| --- | --- | --- | --- | --- |
| 1 | Turn 恢复载荷 | 每个成功 Turn 保存完整 post-state snapshot | 最低复杂度、最高恢复可靠性、最易审计 | 暂不采用 patch 或混合方案；未来只有真实容量或 I/O 数据证明必要时再重审 |
| 2 | Working State JSON 顶层 | 一个带 `format_version` 的 `state_json`，包含五类语义区及 direction/material/draft/review | 原子、紧凑，避免多 JSON 列漂移 | 不拆成多列 JSON，不复制完整 Transcript |
| 3 | Evidence Reference 位置 | 放在 Working State JSON 的相应对象内，Turn snapshot 随状态保存 | 六表内自然表达一对多证据和纠正，不增表 | 由领域校验层验证引用，Phase 1 不增加 Evidence 表 |
| 4 | ReadyContent 与 Session | 完成态严格一对一；ACTIVE 为 0，READY 恰好 1 | 符合 READY 永久结束和修改新建 Session | ReadyContent 不可修改，不允许一个 Session 多版本最终产物 |
| 5 | Session 生命周期 | 仅 `ACTIVE`、`READY` | 满足当前主链，避免提前引入无契约状态 | 暂不加入 cancelled、failed、archived 或 reopen |
| 6 | ID 类型 | 内部实体统一使用规范化 UUIDv4 TEXT；`client_message_id` 为调用方稳定非空字符串 | 使用标准能力，避免新增 UUIDv7／ULID 依赖 | 首个迁移后不得无兼容方案更换 ID 格式 |
| 7 | 时间与版本规则 | 时间统一为 `YYYY-MM-DDTHH:MM:SS.sssZ`；状态版本从 0，格式版本从 1，整数单调递增 | UTC 无歧义、跨语言、可审计 | 不接受本地时间、可变精度或任意时区偏移写法 |
| 8 | SQLite JSON 边界 | 规范化 UTF-8 JSON TEXT；数据库校验合法对象，应用按版本深校验并计算 SHA-256；不使用 SQLite JSONB | 可移植、可审计，领域语义不塞入脆弱 SQL | JSON 规范化和哈希字节规则在执行契约中精确定义 |

## 11. 暂不设计的内容

- 可执行建表 SQL、迁移编号、迁移回滚或 SQLite 文件位置；
- ORM 模型、Repository 实现和具体触发器 SQL；
- 完整 API、HTTP 状态、详细错误码和前端 pending 表示；
- 内部步骤上限默认值、模型超时、重试策略和 Prompt；
- 大型测试清单、容量阈值、Checkpoint 生成频率和保留算法；
- Session 删除、归档、取消、失败终态或 reopen；
- 多租户账号表、Workspace／Project 表或现有文件项目存储迁移；
- 通用 Event、Pending Turn、Outbox、Lease、任务队列和分布式锁；
- Fact、Constraint、Evidence 独立关系表；
- Memory、Knowledge Retrieval、向量存储、素材库或 Production Adapter；
- `owner_confirmed_material`、`trusted_structured_source` 的实现；
- 多模型 Provider、成本计量或 Agent Runtime；
- PostgreSQL 或多实例部署实现。

## 12. 是否具备进入执行契约设计的条件

**具备。** 本 Schema 已完成审核，初始化矛盾已修正，八项重大持久化决定已经确认，可以进入 Director Core Phase 1 执行契约设计。

下一阶段只落实：

- 规范化请求和 JSON 规范化字节规则；
- 六表的声明式约束与最小触发器行为；
- 成功 Turn 的精确事务顺序和 CAS 失败处理；
- Working State／Turn／Checkpoint／ReadyContent 各 JSON 的版本 1 结构验证；
- snapshot 恢复与 Evidence Reference 归属校验；
- 普通 Session 与修改 Session 的版本 0 初始化契约。

当前仍不直接进入编码。执行契约完成并审核通过后，再统一创建迁移和实现 Director Core Phase 1。

---

```text
文档状态：FINAL PASS
本次修改：修正普通 Session 与修改 Session 的版本 0 初始化差异；确认八项重大 Schema 决定；固定 UUIDv4、UTC 毫秒时间格式、snapshot、state_json、Evidence Reference、ReadyContent 一对一、两态生命周期和 JSON 校验边界。
是否新增表：否，仍为固定六表。
是否修改代码：否
是否创建迁移：否
是否 commit：否
是否 push：否
下一阶段：Director Core Phase 1 执行契约设计
```
