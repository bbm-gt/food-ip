# Food-IP Director Core Architecture Amendment 002 — Script Core Product Interaction

## 1. 修订范围与权威关系

本 Amendment 定义单条口播脚本内核的产品交互与首次响应 v2。它只修订 Phase 1 Execution Contract 中“首次响应固定为 v1”和无法持久化方向选择交互的部分，不修改 Core Architecture、Minimal SQLite Schema、Architecture Amendment 001 的其他条款，也不修改既有六表、成功 Turn 原子事务、Evidence、幂等、恢复或 `ReadyContent` v1 契约。

正式文档内部顺序为：Core Architecture、Minimal SQLite Schema、Architecture Amendment 001、Execution Contract、Architecture Amendment 002。若条款冲突，仅以本 Amendment 明确修订的交互和响应版本条款为准。

## 2. 当前产品范围

Food-IP 的长期目标是成为餐饮老板的长期 AI 内容编导，但本轮只优化老板主动发起的一条内容 Session：

- 不实现自动学习或把 AI 推断自动写入老板档案；
- 不实现主动推荐或定时推送；
- 不实现拍摄、剪辑或发布链路；
- 不新增数据库表，不改变一个 `DirectorSession` 只服务一条内容、`READY` 后结束的生命周期。

未来轻档案、内容历史和受控记忆属于独立长期关系上下文层，只能按需向新 Session 提供已确认上下文。

## 3. 老板可见交互

### 3.1 双入口

首次输入支持两种老板意图：

- “帮我找方向”：AI 根据当前已确认信息判断值得继续的内容机会；
- “我已有想法”：AI 先判断老板想法是否值得继续，不能默认直接写稿。

入口是请求意图，不是新的 Workflow 状态。内部仍从 `EXPLORE` 开始。

### 3.2 正式方向卡

需要老板选择方向时，EXPLORE 必须交付恰好三张正式方向卡：一个首推、两个具有实质差异的备选。每张卡包含：

```json
{
  "id": "stable-item-id",
  "direction": "方向原文",
  "reason": "为什么值得拍",
  "recommended": true
}
```

三个 `id` 在该成功 Turn 提交后稳定；恰好一个 `recommended=true`。方向是 AI Judgment，不是 Owner Fact。只有老板选择后，选中的方向才成为 Working State 中获得老板确认的当前方向，并且必须引用包含方向原文的 OWNER Message 作为 Evidence。

选择请求必须携带当前 Session 的方向 ID。服务端验证该 ID 属于当前 Working State 的可选方向；未知、过期或其他 Session 的 ID 一律拒绝。老板可见确认消息必须包含所选方向原文，例如“我选择这个方向：……”，不能只保存不透明 ID。

### 3.3 最少追问与最终交付

- DEEPEN 每轮最多提出一个真正影响成稿的关键问题；当前事实、约束或最近对话已经回答的内容不得重复询问。
- 真实素材足以支撑核心表达时必须立即进入 CREATE，不能为了补全模板继续提问。
- CREATE 只生成一个标题和一篇口播稿。老板未指定时，自适应控制为约 30–60 秒；不生成三个成稿，不编造经营事实，不默认使用广告套话。
- REVIEW 在后台检查方向、事实边界、自然表达、吸引力和口播可用性。表达问题自动回 CREATE，素材问题回 DEEPEN 并只问一个关键问题，方向问题回 EXPLORE 并重新提供三张方向卡。
- 存在支撑成稿所必需但尚未确认的事实时不得进入 READY。
- 前台 READY 只展示标题和口播稿。为兼容 `ReadyContent` v1，内部仍写入 `shooting_notes: []`。

## 4. 首次响应 v2

成功 Turn 的 `first_response_json` 新增 v2：保留 v1 全部字段，并增加可空的 `interaction`：

```json
{
  "format_version": 2,
  "session_id": "uuid",
  "turn_id": "uuid",
  "state_version": 1,
  "director_message_id": "uuid",
  "director_message": "请选择一个更想继续的方向。",
  "run_control": "WAITING_FOR_OWNER",
  "ready_content_id": null,
  "interaction": {
    "kind": "DIRECTION_SELECTION",
    "options": [
      {"id": "uuid", "direction": "...", "reason": "...", "recommended": true},
      {"id": "uuid", "direction": "...", "reason": "...", "recommended": false},
      {"id": "uuid", "direction": "...", "reason": "...", "recommended": false}
    ]
  }
}
```

非方向选择响应的 `interaction` 为 `null`。服务端必须严格校验三项、稳定 ID、非空文案和唯一首推。首次响应 v2 与 Working State 在同一成功 Turn 原子事务中持久化；刷新后的展示、同消息 ID 重试与幂等回放必须返回同一组卡片及 ID，不得重新调用模型或重新生成 ID。

历史 `response_format_version=1` 和 v1 JSON 继续可读、可恢复、可回放；映射到当前 HTTP 响应时 `interaction=null`。本修订不批量迁移历史数据。

## 5. HTTP 契约增量

`DirectorTurnResponse` 添加可空的 `interaction` 字段，结构与上节一致。方向选择仍使用既有消息接口，通过请求 `parameters` 表达结构化动作：

```json
{
  "client_message_id": "uuid",
  "expected_state_version": 1,
  "content": "我选择这个方向：方向原文",
  "parameters": {
    "action": "SELECT_DIRECTION",
    "direction_id": "stable-item-id"
  }
}
```

首次双入口可通过 `parameters.entry_mode` 使用 `DISCOVER` 或 `IDEA`。请求格式 v1 原先只接受空 `parameters` 的条款在本修订范围内扩展为三个严格白名单形状：空对象、单字段 `entry_mode`，或仅含 `action=SELECT_DIRECTION` 与 `direction_id` 的选择动作；其他形状继续拒绝。该增量不改变请求 hash、规范化、幂等和恢复规则，不新增 Session Schema，也不能绕过老板可见原文和 Owner Evidence。

## 6. 兼容与非目标

- 保持 `ReadyContent` v1、Legacy API、现有项目数据、Materials、Timeline、FFmpeg 和 Export 契约不变。
- `semantic_only` 成为新产品默认 stage mode；`legacy` 仅保留显式回退兼容，前台不再默认使用。
- 不新增第七张表，不修改已有迁移，不创建长期 Memory、Retrieval、Router、Multi-Agent 或固定评分系统。
- 自动化测试必须 mock 模型，不产生付费调用。脚本创意质量是否达到产品目标由用户在本地服务中最终验证，自动化测试只证明合同、边界和恢复行为。
