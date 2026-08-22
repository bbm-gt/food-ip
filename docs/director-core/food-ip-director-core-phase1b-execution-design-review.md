# Director Core Phase 1B 最小执行语义与编码范围设计审查

审查范围：仅 Director Core Phase 1B 的持久化执行层。审查基线为
`a7810fb27d13facbeffda56e90ffc4e4e5c57ad4`（分支
`agent/director-core-phase1-design`）。本报告不修改业务代码、Migration、JSON
契约或测试代码，也不包含 API、Orchestrator、Stage Handler、模型、Checkpoint
生产、Memory、Knowledge、素材或多进程方案。

权威依据按任务指定顺序为 Core Architecture FINAL、Minimal SQLite Schema FINAL、
Amendment 001、Execution Contract FINAL 与 `AGENTS.md`；正式设计优先。

## 1. 当前 Phase 1A 实现盘点

Phase 1A 已提供了可直接复用的六表基础、严格 JSON v1、读时完整性验证与版本 0
初始化；尚没有成功 Turn 写入、CAS、幂等执行、事务消歧或恢复写回。

| 范围 | 可直接复用的实现 | Phase 1B 用法 |
| --- | --- | --- |
| SQLite 连接与迁移 | `backend/app/director_core/database.py:16-39` 的外键启用、自检、`connect()`、`apply_migrations()` | 所有写连接继续先验证外键；1B 在此基础上配置有界 busy timeout，并显式开启短写事务。 |
| 六表及保护 | `backend/app/director_core/migrations/0001_director_core_phase1.sql:1-226` | 复用 `(session_id, client_message_id)`、`(session_id, post_state_version)`、消息复合外键、不可变性及 READY 触发器；不加表。 |
| Scope 与 Session | `AuthorizationScope`、`SessionRecord`、`get_session()`，见 `repository.py:43-72,202-214` | Session 存在性和 Workspace/Project 隔离的唯一入口。 |
| Session v0 初始化 | `_empty_state()` 与 `create_session()` / `create_revision_session()`，见 `repository.py:83-173` | 无成功 Turn 的确定性恢复应复用同一 v0 构造及 revision 直接来源校验，而不是复制另一份初始状态规则。 |
| 严格 Working State | `WorkingState`、`validate_working_state()`，见 `models.py:233-285` | 写前验证候选 post-state、READY 条件、revision 的 `draft_id = null` 限制。 |
| Turn/response/snapshot 模型 | `TurnExecutionTrace`、`validate_turn_execution_trace()`、`TurnPostStateSnapshot`、`FirstResponse`、`ReadyContent`，见 `models.py:309-465,495-520` | 写前生成并严格校验 trace、snapshot 和首次响应；顶层字段由 trace 的最后一步与 post-state 闭合。 |
| Canonical JSON 与 hash | `canonical.py:19-190` 的 `normalized_request`、`validate_normalized_request`、`canonical_text`、`canonical_sha256`、`state_sha256` | 用既有 LF+NFC 请求规范化和 Canonical JSON v1；不新增幂等字段或 hash 字段。 |
| 读时 Working State 校验 | `get_working_state()`、`_validate_state_turn_link()`、`_validate_session_lifecycle()`，见 `repository.py:216-284` | 正常路径的状态读取与现有完整性错误类型；恢复路径不能直接依赖此方法，原因见第 8 节。 |
| Turn / transcript / ReadyContent 完整性 | `find_successful_turn()`、`_validated_turn_row()`、`_validate_evidence_turn_pair()`、`get_complete_message_turns()`、`_validated_ready_content_row()`，见 `repository.py:489-671,673-698,782-844` | 同 key 回放应复用 `find_successful_turn()`；现有读校验是提交后闭合检查的基础。 |
| Evidence / 继承闭合 | `_validate_evidence_closure()` 与 `_validate_evidence_reference()`，见 `repository.py:333-487` | 事务中在两条 Message 插入后验证当前 OWNER Evidence；复用直接来源的 inheritance 规则。 |
| Checkpoint 读取 | `get_latest_valid_checkpoint()`，见 `repository.py:700-764` | 仅作为锁外、模型前的上下文候选；不进入 Turn 权威事务，也不参加恢复权威来源。 |

现有测试分布在 `backend/app/tests/test_director_core_{migration,canonical,models,repository}.py`。
本次审查实际运行这四个文件：**63 passed**；仅有既存、非业务失败的
`.pytest_cache` 写入权限 warning。它们验证了 v0、revision 继承、canonical/hash、
Read-time integrity、READY 闭合和损坏拒绝，但尚未覆盖 1B 的执行提交或恢复行为。

## 2. Phase 1B 建议新增或修改的文件

下列是获批准后最小编码面；其中 Migration 的事项是 blocker，不应在本次任务中修改。

| 路径 | 新增或修改内容 | 职责边界 | 不应承担的职责 |
| --- | --- | --- | --- |
| `backend/app/director_core/repository.py` | 增加请求预检、首次响应回放、短权威事务、CAS、提交结果消歧、恢复读/写，以及本报告第 3 节的非持久化 dataclass/领域错误。 | 唯一的六表读写与跨表闭合位置；由 Repository 生成 UUID、UTC 毫秒时间、请求 hash、state hash、消息序号和首次响应。 | 不调用模型、不运行阶段循环、不持有或实现进程 Session 锁、不生成 Checkpoint、不定义 HTTP 错误。 |
| `backend/app/director_core/database.py` | 在不改变表结构的前提下，为每连接提供外键后的 busy-timeout 配置和显式短写事务辅助入口（`BEGIN IMMEDIATE`）。具体等待毫秒数仍由运行配置提供。 | SQLite connection policy，保证所有执行连接采用同一最小策略。 | 不承担重试循环、幂等、业务锁、迁移设计或多进程协调。 |
| `backend/app/director_core/models.py` | **不建议改动既有持久 JSON v1 模型或 JSON 契约。**如需类型化执行输入，只使用 `repository.py` 的 Python dataclass，避免把一次性执行命令误当作持久 JSON schema。 | 保持 v1 事实、trace、snapshot、response 与 ReadyContent 的严格边界。 | 不吸收 API 请求、Provider 输出或 Orchestrator 状态。 |
| `backend/app/tests/test_director_core_execution.py`（建议新增） | 仅 1B 执行语义的 file-backed SQLite / 注入失败测试；现有 1A 测试保持其静态完整性定位。 | 验证真实事务、两个连接的竞争、回滚、消歧和恢复。 | 不调用真实模型、外部服务或前端/API。 |
| `backend/app/tests/test_director_core_repository.py` | 可补少量与既有 read validator 紧密相关的回归断言；不要把整套并发/故障注入塞进 v1 读校验文件。 | 保证新执行层未削弱 1A 已有完整性检查。 | 不替代专门的 1B 事务矩阵。 |
| `backend/app/director_core/migrations/0001_director_core_phase1.sql` 或后续受控迁移 | **当前不修改。**第 8 节 BLOCKER 需要确认能否改变 Working State repair guard，才能编码完整恢复。 | 只在获批后解决“受控维护路径”可写性。 | 不加第七张表、Pending Turn、Outbox、Lease 或新状态字段。 |

## 3. 建议的核心接口

以下仅为 Python 接口草案；所有 ID/hash/时间/消息序号均不由外部调用方传入。

```python
from dataclasses import dataclass
from typing import Any, Literal

@dataclass(frozen=True)
class OwnerTurnRequest:
    # 外部调用方提供；request_format_version 当前只接受 1，parameters 当前只接受 {}。
    session_id: str
    client_message_id: str
    expected_state_version: int
    owner_text: str                 # 原始文本，成功时原样写入 OWNER Message
    request_format_version: int
    parameters: dict[str, Any]

@dataclass(frozen=True)
class TurnCommitCandidate:
    # 由事务外执行层产生；已经过领域/JSON 校验，仍须由 Repository 重新闭合。
    director_message: str
    post_state: dict[str, Any]      # post version / stage 由 Repository 依当前权威 N 闭合
    execution_trace: dict[str, Any]
    ready_content: dict[str, Any] | None

@dataclass(frozen=True)
class ReplayedTurn:
    first_response: dict[str, Any]
    replayed: Literal[True] = True

@dataclass(frozen=True)
class TurnPreparation:
    # 未命中时才返回；仅作为模型外候选的输入，不能授权旧候选提交。
    session: SessionRecord
    working_state: WorkingStateRecord
    normalized_request: dict[str, Any]

@dataclass(frozen=True)
class TurnCommitResult:
    first_response: dict[str, Any]
    replayed: bool
    disambiguated_after_uncertain_commit: bool

class DirectorIdempotencyConflictError(RuntimeError): ...
class DirectorStaleStateError(RuntimeError): ...
class DirectorSessionReadyError(RuntimeError): ...
class DirectorCASConflictError(RuntimeError): ...
class DirectorStorageRetryableError(RuntimeError): ...  # BUSY/LOCKED or known rollback
class DirectorCommitIndeterminateError(RuntimeError): ...
class DirectorRecoveryError(RuntimeError): ...

class DirectorRepository:
    def preflight_owner_turn(
        self, scope: AuthorizationScope, request: OwnerTurnRequest
    ) -> ReplayedTurn | TurnPreparation: ...

    def commit_successful_turn(
        self,
        scope: AuthorizationScope,
        request: OwnerTurnRequest,
        candidate: TurnCommitCandidate,
    ) -> TurnCommitResult: ...

    def recover_working_state(
        self, scope: AuthorizationScope, session_id: str
    ) -> WorkingStateRecord: ...

    def find_successful_turn(
        self, scope: AuthorizationScope, session_id: str, client_message_id: str
    ) -> dict[str, Any] | None: ...
```

数据归属明确如下：外部提供 `OwnerTurnRequest`，以及事务外已经形成的
`TurnCommitCandidate`；Repository 从数据库读取 Session、当前 State、历史 Turn、
Message、直接来源 ReadyContent，并权威生成 `turn_id`、两条 message ID、可选
`ready_content_id`、一次提交时间、`normalized_request_json`、所有 canonical JSON 文本、
hash、pre/post version、消息序号与 `first_response_json`。调用方不能提交这些权威生成
字段，也不能提交最终 `first_response_json`。

`preflight_owner_turn()` 先完成规范化以便比对，但不写表；进程内 Session 锁由调用
执行层包住“preflight → 模型/候选 → commit”的整段生命周期。`commit_successful_turn()`
必须重新读取权威 State，不能接受锁外的 `TurnPreparation` 作为提交证明。

## 4. 完整执行顺序

### 普通 ACTIVE Turn

1. 持有同一 Session 的单进程锁；校验 Scope、请求字段、原始非纯空白文本、v1 格式并在内存生成 normalized request/hash。
2. 确认 Session 存在；按 `(session_id, client_message_id)` 查询并完整验证已有 Turn。相同请求即回放，不同请求即 `DirectorIdempotencyConflictError`。
3. 未命中才要求 Session 为 `ACTIVE`；读取并校验 Working State。缺失/损坏/版本不一致时先走恢复；恢复失败即 `DirectorRecoveryError`。
4. 比较恢复后的权威版本和 `expected_state_version`；不等即 `DirectorStaleStateError`，不写表也不调用模型。
5. 在事务外基于该 State 建立候选；模型/阶段循环不属于 1B。候选必须给出最终 DIRECTOR 文本、完整 post-state、trace，且非 READY 时 ReadyContent 为 `None`。
6. Repository 做全部内存闭合：post version 恰为 `N+1`、state/stage/trace/response 格式、hash、两条派生消息序号，以及 Evidence 对预生成 OWNER ID 的候选引用。
7. 以 `BEGIN IMMEDIATE` 开启短事务，重新确认 `ACTIVE`、当前 State 为 N，并按第 5 节写入和 CAS。
8. COMMIT 成功才返回 Repository 生成的 `first_response_json`；随后 Checkpoint 仍是独立 best-effort，不影响这次结果。

### READY Turn

1. 重复普通 Turn 的步骤 1–6，并额外验证 trace 最后一步为 REVIEW `PASSED` + `READINESS_PASSED`，post-state stage 为 READY、Review 与 draft 闭合、`ready_content == post_state.draft.content`。
2. 同一权威事务先插入 Turn、两条 Message，再用 `ACTIVE + N` CAS 写入 READY Working State。
3. 插入唯一 ReadyContent；现有 `director_ready_content_finish_session` 触发器完成唯一 `ACTIVE → READY`。
4. 提交前验证 Session READY、`ready_at == ReadyContent.created_at`、Working State 最新 Turn/版本/hash、Turn response 的 `ready_content_id` 和 ReadyContent/draft 相等。任一步失败回滚全部。

### 幂等回放

1. 先确认 Scope 内 Session 存在；不得先以 READY 拒绝。
2. 查询已有 Turn 并验证它完整有效。
3. 比较 `request_format_version`、`request_sha256` 与 normalized request 的 canonical bytes；三者都相同则原样返回保存的 `first_response_json`，不比较 expected version、不读模型、不写表。
4. 任一请求身份不同即幂等键冲突；READY Session 仅在未命中时才拒绝新请求。

### stale 请求

1. 仅在未命中幂等 Turn 后读取/恢复当前 Working State。
2. 当前版本不等于 `expected_state_version` 即返回 `DirectorStaleStateError`。
3. 不预留 Turn、不写 Message、不写 State；客户端刷新版本后可保持同一 ID 和同一规范化请求重试。

### CAS 冲突和锁竞争

1. 事务内 `UPDATE director_working_state ... WHERE session_id=? AND state_version=N` 必须同时带 `ACTIVE` Session 谓词；影响行数非 1 即回滚。
2. CAS 为零行是 `DirectorCASConflictError`：重新查询同 key；已命中则回放，未命中则丢弃候选、刷新 State 后才允许重新处理。
3. `SQLITE_BUSY` / `SQLITE_LOCKED` 在开始或 COMMIT 前是可重试存储失败；先回滚、关闭当前 transaction，再查询同 key。不得复用旧候选或直接再次模型调用。

### 提交结果不确定

1. 若 COMMIT、连接失效或响应发送阶段无法确认结果，标为 `INDETERMINATE`；不能假定回滚。
2. 丢弃原连接，仍持有 Session 锁时从连接工厂取得新连接；用同一 `(session_id, client_message_id)` 做完整 Turn 校验。
3. 命中且请求完全相同：回放保存的首次响应，返回 `disambiguated_after_uncertain_commit=True`；命中不同身份则冲突。
4. 新连接确认无 Turn：结果为已回滚/未提交；重新读取 State。不得提交旧候选；调用方可用原 ID 从最新 State 重建候选后重试。
5. 无法建立新连接或无法确定查询结果时抛出 `DirectorCommitIndeterminateError`；调用方只能重试相同 ID，不能换 ID。

### Working State 恢复

1. 触发条件：State 行缺失，canonical JSON/strict model/hash/evidence/latest-turn 校验失败，State 版本或 latest Turn 与最大成功 Turn 不一致，或 Session 生命周期与最大成功 Turn 的 post-state 不一致。
2. 不调用模型、不读取 Checkpoint 作为权威；使用只依赖 Session、Turn、Message、ReadyContent 的恢复专用验证，避免调用依赖当前 State 的 `_validated_turn_row()`。
3. 若没有 Turn，确定性重建普通 v0 或 revision v0；若有 Turn，按 `post_state_version DESC` 选择最大版本，验证版本链和最大 Turn 自身 snapshot/消息/response/Evidence/READY 闭合。
4. 恢复同版本、同 stage、same latest Turn 与重算 hash；不插入 Turn、Message、ReadyContent，不推进版本。此步受第 8 节 BLOCKER 约束。
5. 写后重新读取完整校验；成功才回到幂等/stale 正常流程。最大 Turn 或闭合来源无法确定时 fail-closed。

## 5. 事务原子性清单

事务外读取/计算：请求格式与 canonical normalization；Session 存在性和幂等预检；正常 Working State 或恢复；最近历史和可选 Checkpoint；所有模型调用和候选构造；UUID、一次提交时间、canonical JSON/hash、post-state/trace/ReadyContent 的内存校验。锁外候选永远不能替代事务内重新读。

一个 `BEGIN IMMEDIATE` 写事务内必须：

1. 再读 Scope 内 Session、lifecycle 和 Working State，确认 `ACTIVE` 与版本 N；再查同 idempotency key，防止并发已提交。
2. 插入 Turn（Messages 对 Turn 的既有立即外键要求这一步在前）。
3. 插入 OWNER Message（`2N+1`）与 DIRECTOR Message（`2N+2`）。
4. 验证这恰好是一对可见 Message，及当前 OWNER Evidence、历史 Evidence、继承 Evidence 的事务内闭合。
5. 以 `session_id + N + ACTIVE` 执行 State CAS，写 `N+1`、stage、state、hash、latest Turn、同一提交时间；行数必须为 1。
6. READY 时插入 ReadyContent，令现有触发器原子完成 Session READY；再验证 ReadyContent、response、reviewed draft 与 Session 终态。
7. 做最终六表闭合校验后 COMMIT；任何异常 ROLLBACK。

这与 Execution Contract `3.4` 及 Schema FINAL `8.2` 一致。逻辑上“成功 Turn 包含两条 Message”，但实际 SQL 必须先插入 Turn，因 `director_messages` 的复合外键在 Migration `:69` 指向 Turn；Schema FINAL `:484-492` 已明确该次序。

SQLite 策略：每个连接保持 `foreign_keys=ON`，busy timeout 由运行配置传入且有界；短权威事务使用 `BEGIN IMMEDIATE` 尽早取得单写者资格，绝不把模型调用放入其中。单进程竞争测试必须用两个到同一**文件**数据库的独立连接、受控 barrier/钩子在一方候选完成后让另一方提交，不能用 `:memory:` 假装并发。该策略不承诺多进程排序，也不引入 Lease、队列或 Outbox。

## 6. 恢复算法

```text
recover(scope, session_id):
  session = read_scoped_session(session_id)                 # 不经当前 State validator
  rows = read_turns_ordered_by_post_version_desc(session_id)

  if rows is empty:
      assert_no_messages_or_ready_content(session)
      snapshot = deterministic_v0(session, direct_source_only=True)
      validate(snapshot, version=0, stage=EXPLORE)
      return controlled_replace_state(session, snapshot, latest_turn=None)

  assert all rows have unique post_state_version
  max_version = rows[0].post_state_version
  assert ordered structural chain has exactly versions 1..max_version
  # Earlier snapshots need not be healthy, but their structural pre/post chain must exist.
  max_turn = rows[0]
  previous_stage = EXPLORE if max_version == 1 else turn_at(max_version - 1).target_stage
  validate_max_turn_without_current_working_state(
      max_turn,
      pre_stage=previous_stage,
      require_exact_owner_director_pair=True,
      require_request_trace_response_snapshot_hash=True,
      require_evidence_turn_pairs=True,
  )
  snapshot = max_turn.post_state_snapshot
  assert snapshot.state_version == max_version
  assert sha256(snapshot.envelope) == max_turn.post_state_sha256

  if snapshot.stage == READY:
      assert session.lifecycle_status == READY
      ready = read_exactly_one_ready_content(session, created_by=max_turn.id)
      assert ready.final_content_json == snapshot.state_json.draft.content
      assert max_turn.first_response.ready_content_id == ready.id
  else:
      assert session.lifecycle_status == ACTIVE and no_ready_content(session)

  result = controlled_replace_state(
      session, snapshot, latest_turn=max_turn.id, same_version=True
  )
  assert full_normal_working_state_validation(result)
  return result
```

`controlled_replace_state` 必须以短事务完成，且只允许同版本恢复；它不能“以较早
snapshot 退回”，也不能把版本 N 改为 N+1。重复 post version、缺失版本、最大 snapshot
损坏、最大 Turn 不完整、READY/ReadyContent 不闭合，均为 `DirectorRecoveryError`。这正是
Execution Contract `6.2` 的 fail-closed 要求。现有 Schema 对这个伪代码的可写性缺口见
第 8 节。

## 7. 测试矩阵

所有测试使用 mock/fake 候选，不调用真实模型或外部服务。除已有 v1 静态校验外，1B
至少应有下表。

| 测试名称 | 前置状态 | 执行动作 | 预期数据库结果 | 预期错误或返回值 |
| --- | --- | --- | --- |
| `active_turn_commits_closed_pair` | ACTIVE v0 | 提交合法 WAIT_FOR_OWNER 候选 | 1 Turn、OWNER/DIRECTOR seq 1/2、State v1；无 ReadyContent | 新 `first_response_json` |
| `ready_turn_commits_all_records` | ACTIVE vN，READY 候选 | 提交 READY | 两 Message、Turn、State READY vN+1、唯一 ReadyContent、Session READY 同时间 | READY response 与 ReadyContent ID |
| `first_submission_records_idempotency` | ACTIVE v0 | 首次提交 | 单一完整成功 Turn | 非 replay response |
| `same_request_replays_first_response` | 已有成功 Turn（含 READY） | 同 key、相同 v1 normalized request、旧 expected version | 零新增行、零模型/Checkpoint 调用 | 原样 replay |
| `same_key_different_request_conflicts` | 已有成功 Turn | 同 key，文本/parameters/version/hash 任一不同 | 旧数据不变 | `DirectorIdempotencyConflictError` |
| `stale_expected_version_writes_nothing` | ACTIVE v1 | 未命中 key，expected 0 | 无 Message/Turn/State/ReadyContent 改变 | `DirectorStaleStateError` |
| `ready_session_new_request_rejected` | READY | 未命中新 key | 零新增行 | `DirectorSessionReadyError` |
| `two_local_turns_compete_for_same_version` | 文件 DB ACTIVE v0，两连接 | 两个不同 key 同时以 expected 0 提交 | 至多一个完整 v1 Turn，无半行 | 一个成功；另一个 CAS/stale 后可重试 |
| `sqlite_cas_zero_row_is_not_success` | 提交前由另一连接推进版本 | 执行候选的 State UPDATE | 本候选的 Turn/Message 全部回滚 | `DirectorCASConflictError`；不得复用候选 |
| `busy_or_locked_is_retryable_no_partial_state` | 锁住写库 | 尝试 `BEGIN IMMEDIATE` 或 COMMIT | 无本 Turn 行 | `DirectorStorageRetryableError`；先查同 key |
| `rollback_after_turn_insert` | ACTIVE vN | 在 Turn INSERT 后注入 DB 错误 | 无任何 N+1 Turn/Message/State | 已知 rollback 错误 |
| `rollback_after_owner_message_insert` | 同上 | OWNER INSERT 后失败 | 不留孤立 OWNER/Turn | 已知 rollback 错误 |
| `rollback_after_director_message_insert` | 同上 | DIRECTOR INSERT 后失败 | 不留 pair/Turn | 已知 rollback 错误 |
| `rollback_after_evidence_closure` | 当前 OWNER evidence 候选 | 闭合校验注入失败 | 不留本 Turn 任何记录 | 已知 rollback 错误 |
| `rollback_after_state_cas` | ACTIVE vN | CAS 后、COMMIT 前失败 | State 仍为 N，且无 N+1 Message/Turn | 已知 rollback 错误 |
| `ready_creation_failure_rolls_back_everything` | READY 候选 | ReadyContent INSERT/READY trigger 后失败 | Session ACTIVE，State vN，零本 Turn Message/Turn/ReadyContent | 已知 rollback 错误 |
| `uncertain_commit_is_disambiguated_by_same_key` | 注入“COMMIT 后/返回前连接失败” | 新连接查同 key | 仅一个完整成功 Turn | 保存的首次响应，`disambiguated=True` |
| `uncertain_commit_no_turn_requires_rebuild` | 注入明确未提交且原连接报错 | 新连接查同 key | 无新数据 | 不得复用候选；原 ID 可重新 preflight |
| `restore_missing_v0_state` | 无 Turn，Working State 缺失 | 调用恢复 | 确定性 v0、无 Message/Turn/ReadyContent | 恢复后的 State |
| `restore_missing_state_from_max_snapshot` | 完整 Turns 至 vN，State 缺失 | 调用恢复 | State 被同版本 N snapshot 精确重建 | 恢复后的 State |
| `restore_hash_corrupted_state` | vN State hash 损坏，max snapshot 有效 | 调用恢复 | State 重写为 max snapshot/hash/latest Turn | 恢复后的 State |
| `restore_uses_maximum_not_earlier_snapshot` | v1、v2 都有效，State 损坏 | 调用恢复 | 恢复 v2，而非 v1 | 恢复后的 State |
| `corrupt_max_snapshot_fails_closed` | 最大 Turn snapshot/hash 非法 | 调用恢复 | 原 State 不被较早 snapshot 覆盖 | `DirectorRecoveryError` |
| `duplicate_post_version_fails_closed` | 破坏唯一性后制造重复 vN | 调用恢复 | 不写 State | `DirectorRecoveryError` |
| `broken_turn_version_chain_fails_closed` | 缺版本或 pre/post 不连续 | 调用恢复 | 不写 State | `DirectorRecoveryError` |
| `ready_recovery_requires_ready_content_closure` | READY max Turn，缺失/错绑 ReadyContent | 调用恢复 | 不写 State | `DirectorRecoveryError` |
| `failure_never_leaves_partial_authority` | 参数化覆盖所有失败点 | 查询六表及 transcript | 无半 Message、孤立 Turn、部分 State 或 ReadyContent | 对应错误 |

对失败注入应使用测试专用 connection wrapper / monkeypatch 的单语句失败，而不向生产
Repository 引入故障开关。恢复损坏案例仅在测试中受控绕过保护 trigger / FK，验证生产
路径 fail-closed 或受控恢复，不把绕过手段带入运行代码。

## 8. 发现的设计冲突或缺口

| 标记 | 问题与证据 | 对 1B 的影响 |
| --- | --- | --- |
| **BLOCKER** | Execution Contract `6.2:563-569` 要求 State 缺失/损坏时以同一 version 的最大 Turn snapshot 写回，且明确要求 READY 闭合验证。当前 Migration `0001_director_core_phase1.sql:180-197` 却只允许**插入** v0/EXPLORE State，且只允许 `ACTIVE` Session 的 State UPDATE；READY Session 的任何 State 恢复写入都会被 `director_working_state_update_guard` 拒绝。成功 Turn 的 State 行若缺失，v1+ 又无法重新 INSERT。 | 无法实现任务明确要求的“Working State 丢失恢复”和“READY Session 恢复后验证 ReadyContent”，更不能测试它们。需要确认并变更 trigger/维护写入语义（仍可保持六表不变），或修改已确认的恢复契约为只读 fail-closed；两者都是 schema/恢复语义决策。 |
| **NON_BLOCKER** | 现有 `_validated_turn_row()` 在 `repository.py:596-671` 调用 `_validate_turn_ready_closure()`，后者又依赖当前 Working State。因此它不能直接验证损坏 State 的最大 Turn 作为恢复源。 | 1B 应新增一个不读取当前 Working State 的恢复专用 Turn/snapshot validator，并以 snapshot 的 draft 与 ReadyContent 交叉验证。无需新表、字段或 JSON 契约。 |
| **NON_BLOCKER** | `database.py:29-33` 当前只启用外键，未设 busy timeout，也没有显式 transaction mode。 | 1B 可以在获批编码时以运行配置接入有界 timeout 与 `BEGIN IMMEDIATE`；具体毫秒数已被 Execution Contract 作为 Deferred 参数，并非架构缺口。 |
| **NO_ISSUE** | 成功 Turn SQL 次序并非 OWNER/DIRECTOR 在 Turn 前。Migration `:69` 的即时复合外键要求 Turn 先插入；Schema FINAL `8.2:484-492` 已将此确认为无循环外键的最小次序。 | 事务提交仍保持两条可见 Message 的原子性与“成功 Turn”语义；不需要延迟外键或 schema 更改。 |
| **NO_ISSUE** | 幂等所需的 request format、canonical normalized JSON、SHA-256 和 `first_response_json` 均已存在于 `director_turns`，并且 `canonical.py:150-166` 已实现 v1 规范化。 | 直接复用；不需幂等表或新字段。 |
| **NO_ISSUE** | READY 的一对一关系和 `ACTIVE → READY` 已由唯一约束、`director_ready_content_finish_session` trigger，以及现有 read validators 闭合。 | 1B 可在同一事务采用现有 ReadyContent 插入路径；不需要新状态或手工 reopen。 |

## 9. 最终结论

**BLOCKED_PENDING_DECISION**

原因是第 8 节的 BLOCKER：当前 Migration 的 Working State insert/update guards 与已确认的
同版本恢复语义相互矛盾。必须由产品/架构决策确认以下其中一项，才能开始完整 Phase 1B
编码：

1. 在保持六表不变前提下，批准一个受控的 Working State repair 写入路径（包含 v1+ 缺失
   行重建与 READY 同版本恢复），并批准相应 migration/trigger 变更；或
2. 修改 Execution Contract 的恢复语义，明确对这些情形只读 fail-closed、不持久写回。

在未确认前，执行层可以设计和测试普通提交，但不能宣称完成任务要求的恢复、READY
恢复或完整测试矩阵，因此不应进入实现。
