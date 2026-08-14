# Food-IP Director Core Phase 1 执行契约 FINAL PASS

文档状态：FINAL PASS

本稿只定义 Director Core Phase 1 的执行边界、校验顺序、事务语义和 JSON v1 契约，不包含迁移、ORM、Repository、API 路由、业务代码或测试代码，也不增加第七张表。

## 0. 权威来源与不可改变的边界

本稿遵循以下正式权威层级：

1. `food-ip-director-core-phase1-core-architecture-final.md`
2. `food-ip-director-core-phase1-minimal-sqlite-schema-design-final.md`
3. `food-ip-director-core-phase1-architecture-amendment-001.md`；仅在其明确修订范围内覆盖 Core Architecture 旧 Checkpoint 执行条款
4. 本执行契约
5. 仓库根目录 `AGENTS.md` 作为治理约束
6. `.codex/skills/food-ip-engineer/SKILL.md` 仅作为执行辅助

两份 FINAL PASS 文档和 Amendment 001 的正式路径为：

- `docs/director-core/food-ip-director-core-phase1-core-architecture-final.md`
- `docs/director-core/food-ip-director-core-phase1-minimal-sqlite-schema-design-final.md`
- `docs/director-core/food-ip-director-core-phase1-architecture-amendment-001.md`

归档前后 SHA-256 已逐文件比对一致；本文不再依赖桌面文件作为唯一权威来源。

`AGENTS.md` 的治理约束始终有效，任何正式设计文档都不能授权违反它。Skill 不得覆盖 Architecture、Schema、Amendment 或 Execution Contract；若 Skill 与正式设计文档在设计语义上冲突，以正式设计文档为准；若无冲突则继续遵循 Skill 的执行辅助要求。本次不审核、不修改 Skill。

正式文件名为 `food-ip-director-core-phase1-execution-contract-final.md`；本文内容状态为 FINAL PASS。

本稿严格继承以下边界：

- 主链是 `EXPLORE → DEEPEN → CREATE → REVIEW → READY`；REVIEW 必须先诊断 Writing / Material / Direction 根因。
- 只使用六张既定表：`director_sessions`、`director_messages`、`director_working_state`、`director_turns`、`director_context_checkpoints`、`director_ready_content`。
- 每个成功 Turn 严格产生一条 `OWNER` Message 和一条 `DIRECTOR` 最终可见回复。
- 每个成功 Turn 保存完整 post-state snapshot；恢复不得重新调用模型。
- 模型调用不在 SQLite 写事务中；Checkpoint 默认在权威事务提交后 best-effort 写入，主模型调用前确需压缩历史时也可在事务外按需生成。
- Session 只有 `ACTIVE`、`READY` 两态；READY 后不 reopen，修改通过新 Session 引用旧 ReadyContent。
- Working State 是当前唯一状态权威；Raw Transcript 是成功提交消息的原始可见证据；Checkpoint 只是可废弃的历史压缩。
- Owner Fact 只能由老板明确陈述或明确确认的 OWNER Message 支撑；AI Judgment、Knowledge、Checkpoint 和外部信息不能自动升级为 Owner Fact。
- 不引入 Memory、Knowledge、素材库、Multi-Agent、任务队列、Outbox、Lease、PostgreSQL 或通用 Event 表。

本文中的“必须”是 FINAL PASS 执行契约要求；“Deferred”表示后置运行参数，不是 Schema 或架构 blocker；“禁止”表示不得通过实现细节绕开边界。

## 1. 请求契约

### 1.1 逻辑请求字段

一次处理请求的逻辑输入必须包含：

| 字段 | 要求 | 语义 |
| --- | --- | --- |
| `session_id` | 必填，规范化 UUIDv4 TEXT | 目标 Director Session；实际可由路由参数提供，但必须进入同一请求上下文 |
| `client_message_id` | 必填，非空稳定字符串 | 调用方为这次老板消息生成的幂等键；同一逻辑请求的所有重试保持不变 |
| `expected_state_version` | 必填，非负整数 | 调用方读取到的 Working State 版本，是本轮写入的乐观并发前置条件 |
| `owner_text` | 必填，字符串 | 老板本轮原始可见文本；允许自然语言、换行和 Unicode；不能由模型摘要替代 |
| `request_format_version` | 必填，正整数；Phase 1 只接受 `1` | 规范化请求解释版本；存入 `director_turns.request_format_version` |
| `parameters` | v1 必须存在且为对象；当前必须为空对象 | 预留确实影响处理的显式请求参数；不接受未确认参数 |

`session_id`、`client_message_id`、`expected_state_version` 和授权上下文是执行元数据，不进入 `normalized_request_json` 的行为哈希。v1 的行为规范化请求只包含老板可见输入及已展开的有效参数：

```json
{
  "owner_text": "规范化后的老板文本",
  "parameters": {}
}
```

不允许把当前 Working State、Workspace/Project 授权令牌、服务器当前时间、随机数、模型 provider、提示词、内部步骤结果或 Checkpoint 放进该 JSON。它们不是客户端请求身份的一部分。

### 1.2 原始老板文本与规范化请求的边界

必须保留两种不同表示：

1. **原始老板文本**：请求在应用边界完成 UTF-8 JSON 解码后得到的 `owner_text` 字符串。它保留原始字符顺序、空格、首尾空白、换行语义和 Unicode 字符，不做摘要、不由模型改写。成功提交时，它原样写入本轮 `OWNER` Message 的 `content`。失败请求不写入 Raw Transcript。
2. **规范化请求文本**：只用于幂等比较和 `request_sha256` 的确定性表示。v1 将 `CRLF` 与单独的 `CR` 统一为 `LF`，对 Unicode 文本采用 NFC，并保留所有其他空白，不做 trim、压缩空格或大小写转换。`parameters` 展开默认值后按 v1 规则规范化。

因此，两个请求的老板文本若只在换行表示或 Unicode 组合形式上不同，v1 可产生相同规范化请求；同一 `client_message_id` 会被视为同一请求，并回放第一次成功 Turn 的原始 OWNER Message 和首次响应。这是“语义幂等优先于原始字节幂等”的明确取舍。若用户要求逐字节重试身份，需改为不做上述文本规范化并重新确认。

`owner_text` 不能是 `null`、空字符串或只包含 Unicode 空白字符；这属于调用模型前的必拒请求，因为成功 Message content 不能为空。被拒绝的请求不得写入 `director_messages` 或 `director_turns`。

### 1.3 必填文本的纯空白规则

以下必填文本统一拒绝 `null`、`""` 和只包含 Unicode `White_Space` 属性字符的值：`owner_text`、DIRECTOR 最终可见回复、`script_text`、Owner Fact `statement`、Owner Constraint `statement`、Direction `statement`，以及其他契约中标为必填且非空的自然语言字段。`reason`、`explanation`、Checkpoint 条目 `statement` 等若为必填，也必须通过同一规则；实现不得自行采用不同运行时的宽松或扩展空白集合。

合法的原始 `owner_text` 成功提交时必须原样保存，不能 trim、压缩空格或改写；规范化只用于字段校验与幂等，不得覆盖 Raw Transcript 原文。纯空白请求在模型调用前失败，不写入 Message、Turn 或 Working State。

### 1.4 Food-IP Canonical JSON v1 字节规则

Food-IP Phase 1 不引入外部 Canonical JSON 依赖。所有持久化 JSON 和请求 hash 使用 Food-IP Canonical JSON v1：

- 输入必须先通过对应 JSON v1 Schema 深度校验，再进入序列化。
- 允许的 JSON 值类型只有 object、array、string、integer、boolean、null；float、NaN、Infinity、其他数字或未支持类型全部拒绝。
- 重复对象键、无效 Unicode、未配对 surrogate、尾随内容和非法 JSON 全部拒绝。
- 对象键按 Unicode code point 升序排列；数组顺序保持不变。
- 序列化不得包含无意义空白；字符串使用 JSON 标准转义，普通非 ASCII 字符不得强制转成 `\\uXXXX`。
- 输出必须是严格 UTF-8、无 BOM；所有整数必须处于 SQLite 有符号 64 位范围内。
- 只有 `owner_text` 和已确认的文本请求参数在进入 Canonical JSON 前执行 `CRLF`/`CR` → `LF` 与 Unicode NFC；UUID、枚举、JSON key 和其他领域标识不执行额外 Unicode 变换。

Python 参考实现的字节结果应等价于：

```python
json.dumps(
    value,
    ensure_ascii=False,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

但不得依赖 `json.dumps()` 默认参数；序列化前必须显式拒绝 float 和非法类型，并显式检查重复键、Unicode、尾随内容与 SQLite 64 位整数范围。该规范是 Food-IP Phase 1 项目级规则；未来跨语言实现若需改变字节规则，必须提升 canonical format version。

### 1.5 SHA-256 计算范围

所有 SHA-256 都使用小写十六进制输出，计算范围必须精确固定：

| 哈希 | 计算对象 | 是否包含外层版本字段 |
| --- | --- | --- |
| `request_sha256` | `normalized_request_json` 的规范化 UTF-8 字节 | `request_format_version` 在表列中，不重复放入 JSON，故不包含 |
| `state_sha256` | `canonical_state_envelope = {"state_version": N, "stage": "...", "state_json": <对象>}` 的 Food-IP Canonical JSON v1 UTF-8 字节 | 不包含 snapshot 外层版本 |
| `post_state_sha256` | 与提交后 Working State 完全相同的 canonical state envelope | 不包含 `snapshot_format_version` |
| Checkpoint `integrity_sha256` | `{"format_version": 1, "session_id": ..., "covered_through_seq": ..., "checkpoint_json": <对象>}` 的 Food-IP Canonical JSON v1 UTF-8 字节 | 包含 Checkpoint 信封的 `format_version` |

`state_json` 在哈希信封中是 JSON 对象，不是数据库 TEXT 字符串；数据库中保存的 TEXT 必须本身就是 Food-IP Canonical JSON v1。重新读取时必须重建同一 envelope 后计算，而不是对任意数据库原文直接 Hash。

`first_response_json`、`final_content_json` 和每步 trace 没有既定哈希列。它们必须先通过各自 JSON v1 校验；不得自行增加持久化 hash 字段来替代现有 Schema。

## 2. 幂等与并发契约

### 2.1 幂等预检顺序

在同一 Session 锁内执行：

1. 校验请求结构、授权 Session、`client_message_id` 非空、版本格式和规范化请求。
2. 按 `(session_id, client_message_id)` 查询成功 `director_turns`。
3. 若命中，先比较 `request_format_version`、`request_sha256`，再比较 `normalized_request_json` 的规范化 UTF-8 字节；不能只相信哈希相等。
4. 三者完全一致，直接返回保存的 `first_response_json`，不读取模型、不写 Message、不推进版本、不生成新的 Checkpoint。
5. 任一不同，返回幂等键冲突；不得调用模型，不得覆盖旧 Turn。调用方必须生成新的 `client_message_id`。
6. 只有未命中成功 Turn 时，才继续检查当前 Session 和 `expected_state_version`。

同一 `client_message_id` 只对同一 Session 有幂等含义；跨 Session 重复字符串不是同一个幂等命中。

### 2.2 `expected_state_version` 语义

`expected_state_version = N` 表示调用方希望基于它读取到的版本 N 处理老板消息。它不是请求行为哈希的一部分，也不因同一请求的重试而永久绑定：

- 未命中成功 Turn 且当前版本不是 N：本次尝试是 stale，立即失败，不写入任何权威数据。
- 客户端重新读取当前版本后，可以保留原 `client_message_id` 重试同一规范化请求；服务端会重新进行模型处理和最终 CAS。
- 若原尝试已经成功但响应丢失，重试仍先命中 Turn 并回放，不会因为携带旧 `expected_state_version` 而被 stale 拒绝。

### 2.3 并发责任边界

**单进程 Session 锁负责：**

- 串行化同一 Session 的完整 Turn，包括幂等预检、权威读取、模型调用、内部阶段循环、候选构造和提交。
- 防止同一进程内不同 `client_message_id` 交错使用同一候选状态。
- 降低同一 Session 的重复模型调用和预期外提交冲突。

**SQLite CAS、唯一约束和跨表闭合校验负责：**

- 最终阻止两个执行者同时提交同一 `pre_state_version`。
- 阻止同一 Session 的重复 `(client_message_id)`、重复 post version、消息序号冲突和半个可见回合。
- 在事务开始时重新确认 `ACTIVE` 与当前版本；模型调用前的旧读不能被信任。

Phase 1 只承诺单进程 Session 锁。未来多进程或多实例时，CAS 仍可阻止错误提交，但不能阻止不同实例在提交前重复调用模型，也不定义不同消息的排队顺序；本稿不引入 Lease、队列或分布式锁来补齐该能力。

### 2.4 同一 Session 的并发消息

同一进程内，后到请求等待 Session 锁；锁释放后重新进行幂等预检和权威读取。它不能复用锁外形成的旧上下文或旧候选状态。

不同 Session 可以并行调用模型。不同进程发生冲突时，失败执行者必须丢弃内存候选；若没有权威 Turn 成功提交，原 `client_message_id` 仍可在刷新版本后重试，且必须重新从最新 Working State 组装上下文。

## 3. 成功 Turn 的精确流程

以下是一次成功 Turn 的强制顺序；模型调用和所有内部阶段处理都在事务外。

### 3.0 SQLite 连接与时间前置条件

- 每个数据库连接在任何读写前都必须启用并自检外键约束；无法确认外键约束有效时拒绝所有权威写入。
- 所有内部实体 ID 使用规范化 UUIDv4 TEXT；`client_message_id` 仍按调用方稳定非空字符串规则处理。JSON 内部的 `item_id`、`draft_id`、`review_id`、`turn_id` 和 Message/ReadyContent 引用也必须使用规范化 UUIDv4，除非字段明确标为外部幂等键。
- 所有持久时间使用严格的 `YYYY-MM-DDTHH:MM:SS.sssZ` UTC 毫秒格式；不能使用本地时间、可变精度或带时区偏移的替代写法。一次成功权威事务使用同一个提交时间值写入本轮新增的 Message、Turn、Working State 更新时间以及 READY 时的 ReadyContent/Session `ready_at`。
- `expected_state_version`、`state_version` 和 `post_state_version` 必须在 SQLite 有符号整数范围内；不能接受浮点、字符串、负数或超过实现整数范围的值。
- 权威事务推荐使用能尽早取得 SQLite 写入资格的短写事务和有界 busy 等待；`BUSY/LOCKED` 在写入前或提交时都必须归类为可重试的事务失败，不得在未完成消歧时重新调用模型。

### 3.1 锁内、模型前

1. 获取目标 Session 的进程内锁。
2. 在授权范围内确认 Session 存在；此时不能先要求 Session 为 ACTIVE，因为 READY Session 仍必须允许读取已有 Turn 做幂等回放。
3. 完成 2.1 的幂等预检：命中相同成功 Turn 时立即回放，命中不同请求时立即冲突；只有未命中才继续。
4. 未命中时才要求 Session 为 `ACTIVE`，然后读取权威 `director_sessions`、`director_working_state`、最近成功 Turns、可用 Checkpoint 和最近消息。Working State 正常时优先使用它；Checkpoint 只作为上下文压缩候选，不覆盖状态。
5. 若 Working State 缺失、损坏或 hash 不一致，按第 6 节无模型恢复；恢复不成功则本次请求失败。
6. 确认恢复后的当前版本为 N，并校验请求 `expected_state_version = N`。stale 请求在此结束，不预留成功 Turn。
7. 预生成本轮 `turn_id`、`owner_message_id`、`director_message_id`，均为规范化 UUIDv4。`owner_message_id` 可以立即作为候选 Evidence Reference 使用，但尚未提交。
8. 保存本轮输入的原始 OWNER 文本和规范化请求；原始文本只在内存中等待最终权威事务。

### 3.2 内部阶段循环

1. 以当前权威 Working State、有效历史上下文、可选有效 Checkpoint、最近完整回合和当前老板消息组装当前步骤上下文。
2. 调用当前 Stage 处理器；调用始终在 SQLite 写事务外。
3. 校验处理器结构化结果：运行控制、目标 Stage、转移原因、Gate／REVIEW 诊断必须分字段存在，枚举合法，不能把 `CONTINUE` 当成目标 Stage。
4. 校验当前 Stage 到目标 Stage 的合法转移，并执行对应 Gate。
5. 将通过校验的结果写入候选 Working State 和 execution trace；不写数据库。
6. 若 `run_control = CONTINUE`，必须从最新候选状态重新组装下一步上下文。CREATE 必须看到本轮 DEEPEN 的变化；REVIEW 必须看到本轮 CREATE 的最新 draft。
7. 若 `run_control = WAIT_FOR_OWNER`，结束循环；不再调用模型。
8. 若 `run_control = READY`，必须已经形成通过 Gate 的 READY 候选，结束循环。
9. 超过配置化内部步骤上限即失败，不提交任何部分结果；不能把超限伪装成 `WAIT_FOR_OWNER` 或 READY。上限不写入数据库 Schema；具体默认值 Deferred，待模型、Provider、成本和真实测试明确后决定。

每一步只保存通过校验的阶段决策 trace，不保存完整 Working State snapshot。完整 post-state snapshot 只在本 Turn 的 `director_turns.post_state_snapshot_json` 保存一次。Trace 不保存提示词、模型隐藏推理、原始模型响应全文或自由日志。

### 3.3 完整候选校验

循环结束后，在内存中一次性生成并校验：

- 本轮 OWNER 可见消息，内容为原始老板文本；
- 本轮 DIRECTOR 最终可见回复，必须是非空、面向老板的最终回复；
- post-state version `N+1`、目标 Stage 和完整 Working State v1；
- Turn 最终投影、内部 trace、首次成功 API 响应；
- 本轮所有非继承 Evidence Reference 的闭合关系；
- READY 时的 ReadyContent v1；
- `state_sha256`、`post_state_sha256` 和 snapshot；
- 消息序号 `2N+1`、`2N+2`，以及 Turn 与消息恰好一 OWNER、一 DIRECTOR 的关系。

非 READY 成功 Turn 的最终控制只能是 `WAIT_FOR_OWNER`；`CONTINUE` 只能出现在内部步骤。READY 成功 Turn 必须满足：最终控制为 `READY`、目标 Stage 为 `READY`、Gate 为 `PASSED`、最终 Working State Stage 为 `READY`，并且 trace 最后一次 REVIEW 结果为通过。

### 3.4 短 SQLite 权威事务

候选完整校验通过后才打开短事务，按以下顺序执行：

1. 重新读取并确认 Session 仍为 `ACTIVE`，Working State 当前版本仍为 N；不相信模型前的旧读。
2. 插入 Turn，写入规范化请求、幂等哈希、pre/post version、最终投影、trace、首次响应、完整 post-state snapshot 和 hash。
3. 插入一条 OWNER Message 和一条 DIRECTOR Message，均通过 `(session_id, turn_id)` 指向本 Turn；写入派生消息序号。
4. 在事务内闭合检查：该 Turn 恰好有两条消息，角色恰好为 OWNER 与 DIRECTOR 各一条，Session、Turn 和序号一致。
5. 逐条闭合本轮候选 Evidence：当前候选 OWNER ID 已插入，历史引用指向同 Session 的已提交 OWNER Message，继承引用与直接来源最终状态逐对象一致。
6. 使用 `session_id + expected version N + ACTIVE` 执行 Working State CAS，写入 version `N+1`、新 Stage、`latest_successful_turn_id`、新 hash 和提交时间。受影响行数不是 1 即失败。
7. 若本轮 READY，插入唯一 ReadyContent；其插入前后必须满足同 Session 最新 REVIEW→READY Turn、Working State 和 Session 生命周期的一致性，并完成唯一 `ACTIVE → READY`。
8. 执行提交前最终闭合校验：六表关系、Turn、两条消息、Working State、Evidence、hash、ReadyContent 和 Session 终态全部一致。
9. 任意一步失败，整个事务回滚；不留下 OWNER、DIRECTOR、Turn、Working State 新版本、ReadyContent 或成功幂等记录。
10. 提交成功后才向调用方返回 `first_response_json` 对应的成功响应。

模型调用、内部循环、Checkpoint 生成都不在该 SQLite 写事务中。

若 COMMIT、连接关闭或响应发送的结果不确定，执行者必须把本次结果标记为 `INDETERMINATE`，丢弃/关闭原连接并在仍持有 Session 锁时使用新连接重新查询同一 `(session_id, client_message_id)`：命中相同请求则回放，命中不同请求则冲突；只有确认原连接已关闭且新连接没有该 Turn，才可把结果判定为 `ROLLED_BACK` 并重新读取最新 Working State。唯一约束冲突、BUSY/LOCKED 或 CAS 异常也必须先回滚并重读幂等键及当前版本，不能直接复用旧候选或直接再次调用模型。

### 3.5 Checkpoint 的按需、事务后 best-effort

不得在每个成功 Turn 后固定生成 Checkpoint。只有当前没有有效 Checkpoint、覆盖范围明显落后、下一次上下文组装将超过预算，或需要重建损坏/废弃 Checkpoint 时，才按需生成或更新。权威事务成功后，只读取已提交的 Messages、Turns 和 Working State，确定一个完整回合末尾的覆盖边界，组装 Checkpoint 上下文，并可调用模型生成结构化 Checkpoint。主 Turn 模型调用前若已确定必须压缩历史，也可以先按同一规则生成；该生成仍在任何成功 Turn SQLite 写事务之外。模型调用不改变 Working State。生成结果必须通过严格 JSON v1 与 provenance 校验，再以独立短事务写入。生成、校验、写入或后续废弃失败：

- 不改变成功 Turn 的响应；
- 不回滚两条 Message、Turn、Working State、ReadyContent 或 Session READY 状态；
- 不可把 Checkpoint 失败报告为模型失败或 Turn 失败；
- 后续可从权威 Transcript、Turn snapshot 和 Working State 重建，不需要重新调用主 Turn 模型；Checkpoint 也可被废弃并重新生成。

### 3.6 Model Context Assembly

每个内部步骤调用前都必须从最新候选状态重新组装上下文，顺序固定为：

1. Director Core 固定规则与当前 Stage 契约；
2. 当前完整 Working State；
3. 当前 OWNER Message 原文，完整保留；
4. 修改 Session 的直接来源 ReadyContent 基线，仅在当前判断确实相关时加入；
5. 最新有效 Checkpoint，表示其 `covered_through_seq` 覆盖的历史前缀；
6. Checkpoint 边界之后的完整成功 Turns，按 OWNER/DIRECTOR 成对加载；
7. 对当前 Working State、draft、review 和本轮候选中所有 Evidence Reference 定点回取其对应的已提交 OWNER Message 原文，即使该消息已被 Checkpoint 覆盖；
8. 当前 Turn 内已经通过验证的内部步骤结果和最新候选状态。

硬性规则：

- 每个步骤都从最新候选 Working State 重新组装；CREATE 必须看到本轮 DEEPEN 更新，REVIEW 必须看到本轮 CREATE 最新 draft。
- 当前 OWNER Message 永远不能被摘要、裁剪或用 Checkpoint 替代；当前 Working State 永远不能由 Checkpoint 替代。
- 已被 Checkpoint 覆盖的旧 Raw Transcript 正常情况下不重复整段放入上下文，但 Evidence Reference 指向的 OWNER 原文必须定点回取；Checkpoint 之后的历史必须按完整 Turn 加载，不能加载半个回合。
- 不固定具体 token 数字；实现只接收配置化预算接口，并按以下优先级处理超限：减少已覆盖历史原文 → 将较旧且尚未覆盖的完整 Turns 纳入新 Checkpoint → 保留最近完整 Turns → 保留当前 OWNER Message、Working State、draft/review 和阶段规则。
- 仍超限时返回明确的 Context Assembly 失败，不得静默截断受保护内容，也不得把失败伪装成 `WAIT_FOR_OWNER`。
- Checkpoint 缺失或失效时，若未覆盖历史可在预算内完整加载则直接使用 Raw Transcript；否则必须在主模型阶段前尝试从权威历史重建 Checkpoint。重建失败返回可重试的 Context Assembly 错误，不产生成功 Turn。

## 4. 六张表的声明式约束与最小触发器职责

本节只描述执行契约需要保护的行为，不提供可执行 SQL，不改变既定六表字段，不增加新表。SQLite 负责数据库天然可表达的不变量；复杂领域闭合全部由应用层在同一权威事务内完成。触发器不得依赖插入顺序来阻止正常事务的中间状态。

### 4.1 `director_sessions`

- 声明式 `CHECK`：生命周期只能为 `ACTIVE` 或 `READY`，并保持 `ready_at` 与状态的基本一致；来源 ID 的基本格式有效。
- 声明式唯一/外键：来源 ReadyContent 存在且位于同 Workspace/Project；来源不能自引用；关系字段和创建时间不可变。
- 最小触发器：拒绝生命周期反向转移、来源关系修改和明显跨 Session 错绑；不扫描祖先、不判断继承对象语义、不负责完整 READY 事务闭合。

### 4.2 `director_messages`

- 声明式 `CHECK`：`visible_role` 只能为 `OWNER` 或 `DIRECTOR`；序号为正整数。
- 声明式唯一/复合外键：`(session_id, message_seq)`、`(session_id, turn_id, visible_role)` 唯一；Message 必须指向同 Session 的 Turn。
- 最小触发器：禁止 UPDATE/DELETE，阻止明显跨 Session 错绑；序号和角色配对由声明式约束与应用层最终闭合，不能在插入半回合时阻止正常事务中间状态。

### 4.3 `director_working_state`

- 声明式唯一/外键：每个 Session 恰好一行；`latest_successful_turn_id` 只能指向同 Session 的成功 Turn。
- 声明式 `CHECK`：版本非负整数，Stage 属于五阶段，`state_json` 为对象文本，格式版本受支持。
- 最小触发器：阻止明显跨 Session 错绑，并保护数据库级不可变关系；Working State 本身是可变当前投影，正常成功 Turn 和受控恢复必须允许应用层 CAS UPDATE。触发器不计算 hash、不做 JSON 深校验、不承担 CAS 或业务路由。

### 4.4 `director_turns`

- 声明式唯一/`CHECK`：`(session_id, client_message_id)`、`(session_id, post_state_version)` 唯一；`pre_state_version >= 0`；`post_state_version = pre_state_version + 1`；最终控制为 `WAIT_FOR_OWNER` 或 `READY`。
- JSON 列必须是支持版本的对象文本；Turn 成功记录不可 UPDATE/DELETE。
- 最小触发器：仅阻止明显跨 Session 错绑；重复幂等键和重复 post version 已由 UNIQUE 负责，不再重复设计触发器。不验证 Evidence 语义、hash、REVIEW 路由或“两条消息闭合”。

### 4.5 `director_context_checkpoints`

- 声明式 `CHECK`：状态只能为 `VALID` 或 `DISCARDED`，且 `discarded_at` / discard reason 与状态基本一致；内容和 hash 列为不可变记录。
- `covered_through_seq` 必须为正整数；同 Session 的覆盖边界由应用层确认落在已提交完整 Turn 的 DIRECTOR 消息末尾。
- 最小触发器：禁止内容、覆盖边界、格式版本和 hash 被 UPDATE；只允许 `VALID → DISCARDED`，不负责生成摘要、不改变 Working State、不把 Checkpoint 当 Evidence。

### 4.6 `director_ready_content`

- 声明式唯一/外键：`UNIQUE(session_id)` 与 `UNIQUE(created_by_turn_id)` 保证 ReadyContent 严格一对一；生产 Turn、Session、Working State 同 Session。
- 声明式 `CHECK`：内容格式版本受支持，内容为对象文本；内容不可变。
- 最小触发器：禁止 UPDATE/DELETE，阻止明显跨 Session 错绑，并保持 READY 与 `ready_at` 的基本一致；一对一由 UNIQUE 负责，不再重复设计触发器；不负责 `final_content_json` 深校验、与 PASSED draft 相等、Gate/Stage/root cause 或完整 Session 生命周期闭合。

### 4.7 应用层事务闭合

以下全部由应用层领域校验在同一权威事务中完成：Turn 恰好一 OWNER 与一 DIRECTOR、消息序号与 post version、Working State CAS、JSON 深度校验、hash 计算、Evidence 语义、继承对象逐对象一致、REVIEW root cause 与目标路由、ReadyContent 等于 PASSED draft，以及 `ACTIVE → READY` 的整体闭合。不得把这些复杂语义偷偷下沉为依赖插入顺序的触发器。

## 5. JSON v1 校验契约

所有 v1 JSON 都必须是合法 JSON object，版本未知必须拒绝而不是猜测读取。对象键、数组项和字符串含义按下列契约校验；数组中需要稳定身份的项使用规范化 UUIDv4 `item_id`。不得在 Working State 中复制完整 Transcript、`messages`、`conversation_history` 或完整历史草稿数组。

### 5.1 Working State v1

顶层必须且只能包含：

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

字段规则：

- `format_version`：必填整数 `1`，不可为字符串或 null。
- 五个数组：必填数组，可为空；同一数组内 `item_id` 不重复。
- `direction`：`null` 或当前唯一活跃方向对象。非 null 时字段集合固定为 `item_id`、`statement`、`owner_confirmed`、非空 `evidence_refs`、`inherited_from`；`owner_confirmed` 必须为 true，未确认候选只能留在 `ai_judgments`。
- `material_state`：必填对象，不得为 null；字段集合固定为 `status` 和 `required_confirmations`。`status` 只能为 `UNKNOWN`、`SUFFICIENT`、`INSUFFICIENT`；它是素材缺口投影，不是素材库。
- `required_confirmations` 的每项字段集合固定为 `item_id`、`statement`、`reason`、`evidence_refs` 和 `inherited_from`；`evidence_refs` 必须存在但允许为空数组。它是待确认投影，不是新的事实类别，也不能直接支撑经营事实。
- `draft`：`null` 或单一当前草稿对象。非 null 时字段集合固定为 `draft_id`、`content`、`content_status` 和 `based_on_ready_content_id`（无基线时为 null）；`content_status` 只能为 `WORKING` 或 `FINAL_CANDIDATE`。`content` 字段集合固定为 `title`、`script_text`、`shooting_notes`，与 ReadyContent v1 相同；`draft_id = null` 只允许出现在修改 Session 版本 0，且此时 `review` 必须为 null。第一次实际改写或重新生成时必须生成新的 UUIDv4，之后不得重新变回 null。不得保存多候选 Bundle。
- `review`：`null` 或最近有效评审对象。非 null 时字段集合固定为 `review_id`、`outcome`、`root_cause`、`against_draft_id`、`against_content`；`against_draft_id` 必须等于当前 `draft.draft_id`，`against_content` 必须是该 draft.content 的规范化深拷贝。`outcome` 为 `PASSED` 时 `root_cause` 必须为 null，为 `BLOCKED` 时 `root_cause` 必须是 `WRITING_PROBLEM`、`MATERIAL_PROBLEM` 或 `DIRECTION_PROBLEM`。

对象语义：

- `owner_facts` 项字段集合固定为 `item_id`、`statement`、非空 `evidence_refs`、`supersedes_item_ids` 数组和 `inherited_from`（无继承时为 null）。
- `owner_constraints` 项字段集合固定为 `item_id`、`statement`、非空 `evidence_refs`、`constraint_kind` 和 `inherited_from`；`constraint_kind` 为 `BUSINESS_OBJECTIVE`、`CONTENT_REQUIREMENT`、`PREFERENCE`、`EXPRESSION`、`SHOOTING` 或 `PROHIBITION`。
- `ai_judgments` 项字段集合固定为 `item_id`、`judgment_kind`、`statement`；`judgment_kind` 为 `DIRECTION_CANDIDATE`、`STRUCTURE`、`EXPRESSION` 或 `MATERIAL_ASSESSMENT`；不得带 `owner_confirmed = true`，不得在其中建立第二个 `is_current` 方向权威。
- `unconfirmed_inferences` 项字段集合固定为 `item_id`、`statement`、`reason`；不得进入 `owner_facts` 或 `direction`，除非当前或历史 OWNER Message 明确确认并通过下一轮校验。
- `rejected_items` 项字段集合固定为 `item_id`、`item_kind`、`statement`、`rejection_code`、原 `evidence_refs`、`rejected_by_evidence_refs`、`superseded_by_item_id`（无替代时为 null）和 `inherited_from`；`item_kind` 为 `OWNER_FACT`、`OWNER_CONSTRAINT`、`DIRECTION`、`AI_JUDGMENT` 或 `UNCONFIRMED_INFERENCE`；`rejection_code` 封闭枚举为 `OWNER_CORRECTED`、`OWNER_REJECTED`、`DIRECTION_REPLACED`、`NO_LONGER_USED`、`INCONSISTENT_WITH_CURRENT_STATE`；新事实通过 `supersedes_item_ids` 指向旧项。
- `inherited_from` 字段集合固定为 null 或 `{ "source_ready_content_id": "<UUIDv4>", "source_session_id": "<UUIDv4>" }`；只允许直接来源，不递归扫描祖先。

`rejected_by_evidence_refs` 的条件规则：

- 当 `item_kind` 为 `OWNER_FACT`、`OWNER_CONSTRAINT` 或 `DIRECTION`，且 `rejection_code` 为 `OWNER_CORRECTED`、`OWNER_REJECTED` 或 `DIRECTION_REPLACED` 时，数组必须非空，且每条只能引用已提交的 OWNER Message；这些情况表示老板明确纠正、否定、取消或替换，不能由 AI 自行标记。
- 当 `rejection_code` 为 `NO_LONGER_USED` 或 `INCONSISTENT_WITH_CURRENT_STATE` 时，数组允许为空；空数组只表示系统放弃、失去当前价值或当前状态不再采用，不得解释为老板明确拒绝。
- AI Judgment 或 Unconfirmed Inference 被系统放弃时不得伪造 OWNER Evidence；若它们带空数组，仍必须满足 `statement`、`item_kind`、`rejection_code` 和历史 `evidence_refs` 的结构校验。
- `rejected_by_evidence_refs` 非空时只能引用已提交 OWNER Message；空数组不能绕过 Owner Fact、Owner Constraint 或已确认 Direction 的实质否定规则。

合法的 `item_kind × rejection_code` 组合固定为：

- `OWNER_FACT`：`OWNER_CORRECTED`、`OWNER_REJECTED`、`NO_LONGER_USED`、`INCONSISTENT_WITH_CURRENT_STATE`；不允许 `DIRECTION_REPLACED`。
- `OWNER_CONSTRAINT`：`OWNER_CORRECTED`、`OWNER_REJECTED`、`NO_LONGER_USED`、`INCONSISTENT_WITH_CURRENT_STATE`；不允许 `DIRECTION_REPLACED`。
- `DIRECTION`：`OWNER_REJECTED`、`DIRECTION_REPLACED`、`NO_LONGER_USED`、`INCONSISTENT_WITH_CURRENT_STATE`；不允许 `OWNER_CORRECTED`。
- `AI_JUDGMENT`、`UNCONFIRMED_INFERENCE`：只允许 `NO_LONGER_USED`、`INCONSISTENT_WITH_CURRENT_STATE`；不得使用任何声称老板明确拒绝的 code。

`OWNER_FACT`、`OWNER_CONSTRAINT` 和已确认 `DIRECTION` 的原 `evidence_refs` 必须保持非空并继续闭合到其原始 OWNER Evidence；`rejected_by_evidence_refs = []` 不能删除、伪造或替代这组原始 Evidence。上述组合、Evidence 非空性或 provenance 任一不满足时，Working State 候选校验失败，整轮不提交。

Evidence Reference 的字段集合固定为 `evidence_type`、`target_id`、`target_session_id`，且 v1 的 `evidence_type` 只能为 `owner_message`。所有 `item_id`、引用 ID 和数组项字段的 UUID 形式必须符合 3.0。

跨字段不变量：

- 有效 Owner Fact、Owner Constraint 和已认可 direction 的每条 Evidence Reference 必须闭合到 OWNER Message；AI Judgment 和 Unconfirmed Inference 不因建议而自动成为事实。
- `direction` 非 null 时，它的 `item_id` 不得同时作为另一个当前方向副本存在；旧方向必须进入 `rejected_items` 或被明确标为失效。
- `draft_id = null` 时，`review` 必须为 null；REVIEW 只能针对非 null 的当前 `draft_id`；Stage 为 READY 时 `draft_id` 必须非 null。
- `review.against_draft_id` 必须等于当前 `draft.draft_id`，`against_content` 必须与该 draft.content 完全相同；`review.outcome = PASSED` 要求 draft 非 null。
- `stage = READY` 时，direction、draft 和 review 必须非 null，review 必须 PASSED，material_state.status 必须为 SUFFICIENT；READY 的最终内容另由 ReadyContent v1 校验。
- REVIEW 根因到路由是强制映射：`WRITING_PROBLEM → CREATE`、`MATERIAL_PROBLEM → DEEPEN`、`DIRECTION_PROBLEM → EXPLORE`；`PASSED → READY` 仅在 Readiness Gate 通过时成立。根因与 target Stage 不匹配即拒绝。
- 当前 Session 新 OWNER 消息导致事实冲突时，新证据优先，旧事实进入 `rejected_items`；不得覆盖来源 Session 或原始 Message。
- `rejected_items` 的拒绝 Evidence 条件必须通过：违反上述条件、伪造老板明确拒绝、或用空数组标记 Owner Fact/Constraint/Direction 的实质否定时，候选状态校验失败，整轮不提交。

v1 对所有上述 JSON 对象采取严格字段策略：未知字段、缺失必填字段、错误类型和非法 null 均拒绝；未来新增字段或改变必填语义必须提升 format version。所有持久化 JSON TEXT（request、Working State、trace、response、snapshot、Checkpoint、ReadyContent）都必须以对应 v1 规则生成的规范化 JSON 保存，不能依赖数据库或运行时默认序列化。

### 5.2 Turn execution trace v1

`execution_trace_json` 是对象，必须包含：

```json
{
  "format_version": 1,
  "steps": [
    {
      "step_no": 1,
      "entered_stage": "EXPLORE",
      "run_control": "WAIT_FOR_OWNER",
      "target_stage": "EXPLORE",
      "transition_reason_code": "OWNER_INPUT_REQUIRED",
      "gate": null,
      "review": null,
      "candidate_revision": 1
    }
  ]
}
```

`steps` 必须是按 `step_no` 从 1 开始连续递增的非空数组。每个 step 只保存阶段决策，不复制完整 Working State：

```json
{
  "step_no": 1,
  "entered_stage": "EXPLORE",
  "run_control": "CONTINUE",
  "target_stage": "DEEPEN",
  "transition_reason_code": "DIRECTION_CONFIRMED",
  "gate": null,
  "review": null,
  "candidate_revision": 1
}
```

规则：

- `entered_stage`、`target_stage` 只能是 `EXPLORE`、`DEEPEN`、`CREATE`、`REVIEW`、`READY`。
- `run_control` 只能是 `CONTINUE`、`WAIT_FOR_OWNER`、`READY`；只有最后一步可以是 WAIT 或 READY；中间步骤必须 CONTINUE。
- `transition_reason_code` 必须是稳定代码而非隐藏推理或长自由文本；v1 封闭枚举为 `OWNER_INPUT_REQUIRED`、`DIRECTION_CONFIRMED`、`DIRECTION_INVALID`、`MATERIAL_GAP`、`MATERIAL_SUFFICIENT`、`DRAFT_CREATED`、`WRITING_REPAIR`、`REVIEW_PASSED`。
- `gate` 为 null 或字段集合固定为 `outcome`、`gate_code`、`explanation` 的对象；`outcome` 只能是 `PASSED` 或 `BLOCKED`，`gate_code` 封闭枚举为 `DIRECTION_NOT_CONFIRMED`、`MATERIAL_INSUFFICIENT`、`CONTENT_INCOMPLETE`、`FACT_BOUNDARY_UNCLEAR`、`NOT_SHOOTABLE`、`OWNER_VOICE_MISMATCH`、`READINESS_PASSED`，`explanation` 为非空短字符串。不得存固定数字评分。
- trace `review` 为 null 或字段集合固定为 `outcome`、`root_cause` 的对象；`outcome` 只能为 `PASSED` 或 `BLOCKED`，若 BLOCKED 必须有根因三选一；若 PASSED，root cause 必须为 null。
- `candidate_revision` 必须从 1 开始连续递增，只表示候选状态已验证的修订次数，不是数据库 `state_version`；它不要求单独的数据库 hash 列。
- 每个 step 的 `gate`、`review`、目标 Stage 与 reason code 必须和该步验证过的候选转移匹配；最终完整状态只保存在 Turn 的 `post_state_snapshot_json`，不能从 trace 取代。
- trace 不得包含 prompt、模型隐藏推理、工具调用内部消息、模型原始响应或未校验候选。

### 5.3 Turn post-state snapshot v1

`post_state_snapshot_json` 必须是自包含对象：

```json
{
  "snapshot_format_version": 1,
  "state_version": 1,
  "stage": "EXPLORE",
  "state_json": {
    "format_version": 1,
    "owner_facts": [],
    "ai_judgments": [],
    "unconfirmed_inferences": [],
    "rejected_items": [],
    "owner_constraints": [],
    "direction": null,
    "material_state": {"status": "UNKNOWN", "required_confirmations": []},
    "draft": null,
    "review": null
  }
}
```

必填字段为 `snapshot_format_version`、`state_version`、`stage`、`state_json`，且 `snapshot_format_version = 1`。`state_version` 必须等于 Turn `post_state_version`；`stage` 必须等于 Turn `target_stage`；`state_json` 必须是完全相同的 Working State v1 对象。

正常提交闭合时，snapshot hash 必须等于 Turn `post_state_sha256`，并与未损坏 Working State 的 `state_sha256` 相等。发生恢复时不得依赖待修复 Working State 的旧 hash：只校验最大 post version Turn 自身的 snapshot、该 Turn 的 `post_state_sha256`、state JSON、Evidence 和跨表关系；写回 Working State 后再以同一 canonical envelope 重算新 hash，并确认它等于该 Turn hash。snapshot 外层版本不进入 `post_state_sha256`。

### 5.4 Context Checkpoint v1

表列 `format_version = 1`；`checkpoint_json` 必须是覆盖已提交历史前缀的结构化语义摘要，字段集合固定为 `conversation_summary`、`confirmed_owner_positions`、`open_threads`、`abandoned_directions`：

```json
{
  "conversation_summary": "对已覆盖历史的简洁语义摘要",
  "confirmed_owner_positions": [
    {
      "statement": "老板在历史对话中明确表达或确认的立场",
      "message_refs": ["<OWNER message UUID>"]
    }
  ],
  "open_threads": [
    {
      "statement": "仍未解决但可能影响后续创作的问题",
      "message_refs": ["<message UUID>"]
    }
  ],
  "abandoned_directions": [
    {
      "statement": "已经明确放弃的方向及原因",
      "message_refs": ["<message UUID>"]
    }
  ]
}
```

规则：

- `conversation_summary` 必须为字符串；当三个条目数组至少一个非空时必须非空，且只能概括下方带 `message_refs` 的条目；当三个数组全部为空时必须为 `""`。不得独立引入 Owner Fact、未引用的经营事实或隐藏推理。
- 三个条目数组必填且可为空；每项字段集合固定为 `statement`、`message_refs`，`statement` 非空，`message_refs` 为非空 UUIDv4 数组且不重复。
- `message_refs` 只能指向本 Session 已提交的 Message，并且每个目标 `message_seq <= covered_through_seq`；`confirmed_owner_positions` 的每条引用必须指向 OWNER Message；任何其他包含 Owner Fact、Constraint 或老板立场的陈述也必须至少引用 OWNER Message。每条 `statement` 必须经过应用层 provenance 校验，确认引用原文在语义上支持该陈述，不能只校验 UUID 和角色。它们不是 Evidence Reference，不能替代 Working State 内的 Evidence。
- Checkpoint 只读取已提交 Messages、Turns、Working State 和必要的 Turn snapshot；允许事务后模型生成摘要，但必须通过本 Schema 校验。不得保存模型隐藏推理、原始响应或自由日志。
- 不再保存 `completed_turn_ids`；覆盖范围完全由表列 `covered_through_seq` 表达。
- `covered_through_seq` 必须是正整数，落在已成功提交的完整回合末尾；`1..covered_through_seq` 无消息缺口，边界是 DIRECTOR Message。
- Checkpoint 不得覆盖 Working State，不得作为 Evidence Reference。与 Working State 冲突时，以 Working State 和 Raw Transcript 为准；Checkpoint 可废弃、可从权威数据重新生成。
- integrity hash 必须按 1.5 的四字段 envelope 计算；内容、边界和 hash 不可更新，只能废弃并新建。
- 读取时只接受格式支持、状态 VALID、边界闭合、hash 正确的记录；按 `covered_through_seq DESC, created_at DESC, id DESC` 选择，不使用竞争性的 `is_latest`。

### 5.5 ReadyContent v1

表列 `content_format_version = 1`；`final_content_json` 必须是不可变对象，v1 字段集合固定为 `title`、`script_text`、`shooting_notes`：

```json
{
  "title": null,
  "script_text": "完整、自然、可直接表达和拍摄的内容",
  "shooting_notes": []
}
```

`title` 必须存在且为 `null` 或非空字符串；`script_text` 必须为非空字符串；`shooting_notes` 必须为数组，每项为非空字符串。不得包含模型隐藏推理、固定评分、完整 Evidence 原文、多个候选版本或未确认 Owner Fact。

ReadyContent 的 `final_content_json` 必须等于创建该产物的最终 Working State `draft.content` 的规范化深拷贝；不允许审核 draft A 后在提交时转换或替换成内容 B。READY 前若需要格式转换，转换后的对象必须重新成为 draft 并经过 REVIEW PASSED。

ReadyContent v1 的跨表不变量：

- 只由同 Session 最新成功 Turn 创建；该 Turn 为 `REVIEW → READY`，Gate PASSED，最后 REVIEW PASSED。
- Working State Stage 必须 READY，post version、latest Turn、hash 与 Turn 完全一致。
- Session 插入前必须 ACTIVE，插入后恰好 READY；同一 Session 和同一 Turn 不能再生成第二条。
- 内容创建后不可修改；修改必须创建引用它的新 Session。

本结构不强迫所有内容拆成 opening/body/ending 三段；`script_text` 是完整、自然、可直接表达和拍摄的内容。

### 5.6 首次成功响应 `first_response_json` v1

`response_format_version = 1` 时，`first_response_json` 必须是严格对象：

```json
{
  "session_id": "<UUIDv4>",
  "turn_id": "<UUIDv4>",
  "owner_message_id": "<UUIDv4>",
  "director_message_id": "<UUIDv4>",
  "state_version": 1,
  "stage": "DEEPEN",
  "run_control": "WAIT_FOR_OWNER",
  "director_message": "面向老板的最终可见回复",
  "ready_content_id": null
}
```

所有字段必填；`stage` 为五阶段之一，`run_control` 只能为 `WAIT_FOR_OWNER` 或 `READY`；非 READY 时 `ready_content_id` 必须为 null，READY 时必须为同 Turn 创建的 ReadyContent ID；`director_message` 必须与本 Turn DIRECTOR Message content 完全一致；`state_version` 必须等于 Turn post version。该 JSON 按 v1 规范化保存并永久作为幂等回放快照，不包含 prompt、trace 或隐藏推理。

## 6. 恢复流程

### 6.1 Working State 正常读取

每次模型前都读取当前 Working State，并校验：Session 仍 ACTIVE、行存在且唯一、state version 非负、Stage 合法、state_json 是支持的 v1 对象、`latest_successful_turn_id` 与当前版本一致、canonical hash 正确。全部通过时直接作为唯一当前状态，Checkpoint 只用于历史上下文。

### 6.2 Working State 缺失、损坏或 hash 不一致

恢复不得调用模型。按以下顺序执行：

1. 若不存在任何成功 Turn（Working State 应为版本 0）：
   - 普通新创作 Session 确定性重建空的 Working State v1：空 Owner Facts/Constraints、direction null、material_state 初始 UNKNOWN、draft/review null、Stage EXPLORE。
   - 修改 Session 只读取直接来源 ReadyContent 和其生产 Session 的最终 Working State，按版本 0 初始化契约复制允许对象；不读取完整 Transcript，不递归扫描祖先。其 `draft_id` 保持 null，依靠来源 ReadyContent ID 与不可变内容确定性恢复。
   - 重算 state hash，并以受控维护路径写回同一版本 0，不产生 Turn、Message 或 READY 转移。
2. 若存在成功 Turn：按 `post_state_version DESC` 取最大版本的最近成功 Turn，只校验该 Turn 自身的 snapshot 格式、post version、target stage、post hash、Working State v1 和 Evidence Reference。完整 snapshot 是独立恢复载荷，不要求更早 Turn 的 snapshot 也有效。
3. 从有效 snapshot 提取 `{state_version, stage, state_json}`，重算 hash，并确认与该 Turn 的 `post_state_sha256` 一致；再确认 Session 当前生命周期与该状态一致。
4. 以受控维护路径恢复 Working State 的同一版本、Stage、latest Turn 和 hash；恢复不能推进版本，不能插入可见 Message、Turn 或 ReadyContent。
5. 恢复完成后，原请求仍按正常幂等预检和 `expected_state_version` 检查，再决定是否调用模型。

若最大 post version 的最近成功 Turn snapshot 本身损坏，不能把更早 snapshot 降级写成当前版本，也不能跳过该 Turn 继续创作；应 fail closed，返回需要维护的恢复错误，允许在外部修复后使用原 `client_message_id` 重试。任何情况下不得重新调用模型来“猜回”状态。

## 7. Evidence Reference 闭合校验

Phase 1 只接受：

```json
{
  "evidence_type": "owner_message",
  "target_id": "<message UUID>",
  "target_session_id": "<session UUID>"
}
```

### 7.1 当前 Turn 新 OWNER Message

当前 Turn 的 OWNER Message ID 在模型前预生成。候选 Working State 可以引用它，但必须在最终事务中按以下顺序闭合：

1. 本轮引用的 `target_id` 必须等于预生成的 `owner_message_id`。
2. `target_session_id` 必须等于当前 Session。
3. 事务内插入的 Message 必须属于当前 Turn、角色为 OWNER、content 等于本轮原始老板文本、message_seq 等于 `2 × post_state_version - 1`。
4. 在 CAS 前再次查询并确认该 Message 已存在；事务回滚时该候选引用随状态一起回滚。

候选 ID 不能因为预生成就被当作已提交证据；只有同一权威事务插入成功后才闭合。

### 7.2 历史本 Session OWNER Message

历史引用必须满足：

- `target_id` 存在且是规范化 UUIDv4 Message ID；
- 目标 Message 的 `visible_role = OWNER`；
- 目标 Message 的实际 `session_id` 等于 `target_session_id`；
- 对非继承对象，`target_session_id` 必须是当前 Session；
- 目标 Message 属于同 Session 的成功 Turn，且该 Turn 已闭合 OWNER/DIRECTOR 两条消息；
- 同一对象内 Evidence Reference 去重，至少有一条且共同支撑该事实或约束；
- 不能引用失败请求、未提交 Message、DIRECTOR Message、内部步骤消息或 Checkpoint 文本。

### 7.3 修改 Session 的继承 Evidence

修改 Session 只从直接来源 ReadyContent 对应 Session 的最终 Working State 复制契约 B 允许的有效对象：Owner Facts、Owner Constraints、已认可 direction；以来源 ReadyContent 的 `final_content_json` 初始化 draft。继承对象：

- 完整保留原有 Evidence Reference，不重新扫描祖先 Session，也不任意增加来源消息；
- 增加当前直接来源的 `inherited_from.source_ready_content_id` 与 `source_session_id`；
- 可以保留 Evidence 的旧 `target_session_id`，因为这是被直接来源最终状态合法继承的原始证据，不是当前 Session 新造的引用；
- 当前 Session 新增或修改的对象不能借用继承标记绕过当前 OWNER Evidence；
- 不得把 ReadyContent 本身当作 Owner Fact 证据。

### 7.4 必须拒绝的非法引用

以下任一情况都使候选状态或事务失败：跨 Workspace/Project 或跨 Session 的新引用；`target_session_id` 与目标真实 Session 不符；角色不是 OWNER；Message 未提交或属于失败请求；指向 DIRECTOR、Checkpoint、Turn trace、模型输出或系统内部消息；引用当前 Session 之外的消息却没有合法继承对象证明；Evidence 类型不是 `owner_message`；重复引用；对象 statement 语义明显超出证据原意。

最后一项属于领域语义校验，不由 SQLite CHECK 代替；校验失败时整轮不提交。

## 8. Session 版本 0 初始化

Session 创建与唯一 Working State 必须在同一短事务中完成；没有初始 Turn 或 Message。

### 8.1 普通新创作 Session

- 创建 `ACTIVE` Session，`state_version = 0`、`stage = EXPLORE`、`latest_successful_turn_id = null`。
- `owner_facts`、`owner_constraints`、`ai_judgments`、`unconfirmed_inferences`、`rejected_items` 为空；`direction = null`。
- `material_state.status = UNKNOWN`，`required_confirmations = []`；`draft = null`、`review = null`。
- 校验并保存 `state_version = 0`、`state_json.format_version = 1` 的初始状态及其 hash；失败则整个 Session 创建事务回滚。

### 8.2 基于 ReadyContent 的修改 Session

- `source_ready_content_id` 必须指向已提交、已 READY、同 Workspace/Project 的旧 ReadyContent，不能自引用。
- 创建新 `ACTIVE` Session，仍从 `EXPLORE` 和 `state_version = 0` 开始。
- 只从直接来源 Session 的最终有效 Working State 复制：Owner Facts、Owner Constraints、已认可 direction，并保留它们已有 Evidence Reference；每个复制对象增加直接 `inherited_from`。
- 使用原 ReadyContent 的不可变 `final_content_json` 作为 draft.content 的规范化深拷贝，并记录 `based_on_ready_content_id`。版本 0 的 `draft_id` 必须为 `null`，表示当前 Session 尚未产生新的草稿实体；第一次实际改写或重新生成草稿时必须生成新的规范化 UUIDv4，之后不得重新变回 null。ReadyContent 本身不是 Evidence。
- 版本 0 修改 Session 的 draft 固定为 `{ "draft_id": null, "content": { "title": "来源标题（若有）", "script_text": "来源 ReadyContent 的完整 script_text", "shooting_notes": ["来源 ReadyContent 的原有拍摄说明"] }, "content_status": "WORKING", "based_on_ready_content_id": "<source ReadyContent UUID>" }`；示例中的 title/script_text/shooting_notes 只是字段形状，实际值必须是来源 `final_content_json` 的规范化深拷贝，不能丢失标题或拍摄说明。`material_state` 固定为 `{ "status": "UNKNOWN", "required_confirmations": [] }`，`review = null`。这些值与普通版本 0 一样可重建并参与同一 hash。
- 不复制 AI Judgments、Unconfirmed Inferences、Rejected Items、旧 Review 结论或完整 Transcript；material_state 按上述确定性初始对象作为当前任务投影初始化。
- 对象复制必须逐对象比对来源最终状态，确认 statement、Evidence、继承标记没有扩张或替换；不读取来源的祖先 Session。

### 8.3 首轮冲突处理

新 Session 的第一条 OWNER Message 可能与继承对象冲突：

- 新 OWNER Evidence 优先；原继承对象移入当前 Session 的 `rejected_items`，保留原 Evidence 和继承标记，拒绝原因为 `OWNER_CORRECTED`。
- 新事实使用当前 Session 新 OWNER Message Evidence，不沿用旧对象的 `inherited_from`。
- 时间敏感、语境明显变化或无法确认的内容不得直接进入有效 Owner Facts/direction；放入 `material_state.required_confirmations` 的最小待确认投影，并保持原 Evidence 和原因。
- 不修改旧 Session、旧 Message 或旧 ReadyContent，也不把冲突解决隐式写回来源。

## 9. 失败与重试矩阵

“允许原 ID 重试”表示没有成功 Turn 被记录，或已有成功 Turn 可被幂等预检安全回放；它不表示可以复用失败尝试生成的模型候选。

| 情况 | 权威数据结果 | 原 `client_message_id` 是否可用 | 重试要求 |
| --- | --- | --- | --- |
| 模型调用失败、超时或 provider 返回不可用 | 不写 Message、Turn、Working State、ReadyContent | 可以 | 原始请求保持不变；重新读取状态后重新调用模型 |
| 模型输出结构校验失败、非法转移、Gate/JSON/Evidence 校验失败 | 不写任何成功权威结果 | 可以 | 修复或重新生成候选；不得把失败输出写入 Raw Transcript |
| SQLite 权威事务明确回滚 | 整体无成功 Turn | 可以 | 重新读取版本并重新生成候选；不能复用旧候选直接提交 |
| 事务提交结果不确定、客户端丢失响应 | `INDETERMINATE`，可能已提交 | 必须优先使用原 ID | 关闭/丢弃原连接并用新连接查询同一幂等键；命中则原样回放，只有确认回滚且未命中后才刷新状态重试，不能生成新 ID 规避未知提交 |
| CAS 冲突 | 本次候选不提交；另一个执行者可能已成功 | 可以 | 刷新 `expected_state_version`，保留原 ID 和同一规范化请求，重新组装上下文并重新调用模型 |
| stale `expected_state_version`，且该 ID 尚无成功 Turn | 不写入 | 可以 | 刷新版本后可原 ID 重试；不需要新 ID |
| 重复 ID + 相同 `request_format_version`、规范化 JSON 和 hash | 已有成功 Turn | 可以且应该 | 直接回放 `first_response_json`，不调用模型、不写表；此时不比较 `expected_state_version`，READY Session 也必须允许读取回放 |
| 重复 ID + 任一请求身份不同 | 旧 Turn 保持不变 | 不可以 | 必须生成新 `client_message_id`；不能覆盖、合并或重用旧 ID |
| Checkpoint 生成/校验/写入失败 | 成功 Turn 已提交，Checkpoint 缺失或可重建 | 可以 | 原 ID 重试只回放首次响应；Checkpoint 另行 best-effort 重建，不重新调用模型 |
| Working State 恢复失败 | 不调用模型、不提交 | 可以 | 外部修复或最大版本 Turn snapshot 可验证后，用原 ID 重试；不得以新 ID 绕过恢复错误 |

若失败原因是请求本身非法，客户端应先修正请求；是否继续使用原 ID 取决于修正后规范化请求是否改变：改变则必须新 ID，不改变则可以原 ID 重试。任何已成功 Turn 的后续老板消息都必须使用新的 `client_message_id`。

## 10. 完全继承的已确认决定

1. 六张表及各自职责不变，不增加表。
2. 成功 Turn 固定一条 OWNER Message + 一条 DIRECTOR 最终可见回复。
3. 失败请求不进入 Raw Transcript，不产生成功 Turn。
4. Turn 保存完整 post-state snapshot，恢复不调用模型。
5. Working State 使用一个带 `format_version = 1` 的紧凑 `state_json`；Evidence Reference 放在相应对象内。
6. `state_sha256` 和 `post_state_sha256` 针对相同 canonical state envelope；Checkpoint hash 使用独立 envelope。
7. 成功 Turn 的两条消息、Turn、Working State、可选 ReadyContent 和 Session 生命周期在同一短权威事务中原子提交。
8. Checkpoint 默认在权威事务之后独立 best-effort 写入；若主模型调用前确需压缩历史，也可在权威事务之外按需生成；失败不回滚成功 Turn。
9. Session 仅 `ACTIVE → READY`，ReadyContent 不可变，修改从新 Session 引用旧产物。
10. Phase 1 使用单进程 Session 锁和 SQLite CAS；不引入队列、Outbox、Lease、多实例协调或 PostgreSQL。

## 11. 本文新增并已确认的专业决定

1. 请求 v1 使用“原始文本保存、LF + NFC 规范化后做幂等”的双表示；这使展示证据与重试语义各自稳定。
2. 规范化 JSON 使用 Food-IP Canonical JSON v1，所有 hash 输入使用无 BOM 的 UTF-8 字节；不新增第三方依赖。
3. `expected_state_version` 不进入请求行为 hash；stale 后刷新版本可复用原 client ID，未知提交结果也能安全回放。
4. 内部 trace 每步只保存经过验证的阶段决策和递增 `candidate_revision`；完整 post-state snapshot 只保存一次，以控制存储并保持恢复载荷明确。
5. 恢复采取 fail-closed：最大 post version 的单个完整 snapshot 独立恢复，不能用更早 snapshot 降级伪装当前状态，不能在无法确定恢复时调用模型。
6. Checkpoint v1 是覆盖历史前缀的、带 Message 引用的结构化语义摘要；可在权威事务后调用模型生成，但不承担 Evidence 或 Working State 权威，也不保存隐藏推理或自由日志。
7. ReadyContent v1 采用 `title`、`script_text`、`shooting_notes` 的自然内容结构，不强迫三段式，不引入评分或多候选 Bundle；修改 Session v0 的 `draft_id` 保持 null，首次实际改写才生成新 UUIDv4。
8. `required_confirmations` 允许空 `evidence_refs`，并将 Owner Constraint 扩展为六类封闭枚举；缺失信息不能伪装成 Owner Fact。
9. 事务结果不确定时必须先关闭原连接并用新连接消歧；READY Session 仍允许对已有成功 Turn 做幂等回放。

## 12. Deferred 运行参数与非阻塞事项

1. 内部步骤上限的具体默认值 Deferred：必须是配置化运行参数，不写入 Schema；具体数字待模型、Provider、成本和真实测试明确后决定。
2. Context Assembly 的具体预算值 Deferred：只需在编码时注入配置化预算接口，不固定模型 token 数字。
3. SQLite busy 等待的具体时长 Deferred：保持有界等待和失败分类，数值由运行环境测试决定。
4. 模型/Provider 的具体选择不在本文确定，仍受 Phase 1 范围与正式架构边界约束。

以下决定已在本 FINAL PASS 中固定，不再作为用户确认项：Food-IP Canonical JSON v1、LF/NFC 字段级预处理、stale/CAS 后复用原 client ID、ReadyContent 自然结构、修改 Session v0 的 `draft_id = null`、Checkpoint 按需模型生成边界、Required Confirmations 空 evidence、六种 Constraint 枚举、first_response_json v1、未知 COMMIT 消歧、严格纯空白文本拒绝和 SQLite 前置校验。

## 13. 发现的 Schema／架构冲突或缺口

- **没有发现必须修改既有六表 Schema 才能执行本稿的必要项。** `expected_state_version` 是请求级并发前置条件，不需要落表；原始老板文本可由 `director_messages.content` 保存；请求身份、trace、snapshot 和首次响应已有 `director_turns` 字段承载。
- 当前 Schema 没有 `final_content_json` 的哈希列；本稿没有自行添加。只要接受“ReadyContent v1 通过 JSON 校验并依靠不可变语义保护”的决定，就不是执行阻塞；若要求 ReadyContent hash，必须另行确认 Schema 变更。
- 版本 0 没有单独的初始状态快照列；普通 Session 的空初始状态可确定性重建，修改 Session 通过直接来源 ReadyContent 的深拷贝、`draft_id = null` 和 `based_on_ready_content_id` 规则重建，因此本稿不要求加列。
- 单进程锁无法解决多实例重复模型调用和消息排序；这是已确认 Phase 1 部署边界，不应通过本稿偷偷引入 Lease、队列或 Outbox。
- 两份 FINAL PASS 设计文档已复制到 `docs/director-core/`，且 SHA-256 与桌面来源一致；该来源缺口已处理。
- ReadyContent 没有独立 hash 列；本稿用“与最终 PASSED REVIEW 的 draft.content 规范化深拷贝相等”闭合审核对象与最终产物。如果需要单独 hash 或允许转换，必须另行确认 Schema/架构影响。
- Schema FINAL 对 Checkpoint 的既有字段、覆盖边界、不可变性和合法 JSON 对象容器继续有效；其“结构化压缩”和示例不是新增数据库字段或固定业务语义。Amendment 001 与本契约只在该既有 `checkpoint_json` TEXT 容器内确定 v1 内容和生成边界，因此不构成 Schema 决策冲突。
- 当前文档、Amendment 001 和两份 FINAL PASS 均已归档到仓库 `docs/director-core/`，可只依赖仓库内正式文档进入编码。
- 未发现需要修改既有六表 Schema 的执行缺口；没有新增字段、表、依赖或基础设施。
- 内部步骤上限、Context Assembly 预算和 busy 等待具体数值是 Deferred 运行参数，不构成设计 blocker。

## 14. 是否已经足以进入统一编码阶段

**可以开始统一编码。** 本文、Amendment 001、Core Architecture FINAL PASS 和 Minimal SQLite Schema FINAL PASS 已在仓库内闭合；没有 Schema blocker、第三方依赖 blocker 或未解决的架构冲突。内部步骤上限、上下文预算和 busy 等待具体数值属于 Deferred 运行参数，可在模型/Provider/成本/真实测试明确后配置，不阻塞迁移与编码。已消除三段式 ReadyContent、Draft/ReadyContent ID 复用、Checkpoint 无法压缩历史、每步完整 snapshot、Rejected Items 伪造 OWNER Evidence、纯空白输入和复杂 Trigger 下沉等问题。

未执行：编码、迁移、ORM、Repository、API 路由、业务代码、测试代码、commit、push。
