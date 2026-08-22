# Food-IP Director Core Phase 1I 真实内容质量验证

## 1. 状态与边界

Phase 1I 是一次 **Concierge 主实验 + 当前聊天基线** 的现场研究，不是生产功能阶段。目标是验证：面对真实单店老板，Food-IP 能否主动判断下一条最值得拍的内容，以最少追问补足真实素材，并交付老板会在七天内实际录制的 20–60 秒真人短视频。

本阶段保持以下边界：

- 单条内容仍遵循 `EXPLORE → DEEPEN → CREATE → REVIEW → READY`；
- 不修改生产 API、Director Core 六表、`ReadyContent` 或当前前端契约；
- 不新增长期 Memory、Retrieval、Formula、固定评分、Router、多 Agent、发布数据或视频生成；
- 临时研究对象不构成未来 Schema 承诺，产品运行时代码不得引用；
- 老板修改行为只形成 `OBSERVATION_ONLY` 信号，不能自动成为 Owner Facts；
- 研究报告和外部案例只帮助判断，不是当前门店事实来源。

工具实现位于 `scripts/phase1i_quality_validation.py`。真实研究数据必须放在已被 Git 忽略的 `runtime/phase1i/`，不得提交到源码仓库或写入正式产品数据库。

## 2. 样本与完成条件

- 招募 5–10 位真实单店老板，每位只代表一家店；
- 默认至少覆盖三个餐饮品类；
- 参与者必须愿意提供明确事实、接受同一模型处理，并配合七天录制回访；
- 每位老板完成三个真实内容任务：第 1 次使用当前聊天，第 2、3 次使用 Concierge 流程；
- 基线和 Concierge 必须使用研究记录中的同一 `approved_model`；
- 每个有效产物都要完成事实审计、老板反馈和独立专业编导盲审；
- 操作员改写过内容的 Cycle 是协议偏差，不计入行为门槛。

参与者只使用 `P001`–`P010` 编号。研究文件不得保存姓名、手机号、精确地址、人脸、录音、视频或授权原件；`consent_record_ref` 只引用研究负责人另行保管的授权记录。

## 3. 初始化研究

从仓库根目录运行：

```powershell
backend/.venv/Scripts/python.exe scripts/phase1i_quality_validation.py init `
  runtime/phase1i/study.json `
  --study-id phase1i-2026-08 `
  --provider deepseek `
  --model deepseek-v4-flash
```

该命令只建立空的、严格版本化的研究记录。之后由研究负责人加入匿名参与者和 Cycle 数据。每次修改后先校验：

需要在编辑器中查看全部临时字段和枚举时，可导出非生产 JSON Schema：

```powershell
backend/.venv/Scripts/python.exe scripts/phase1i_quality_validation.py schema `
  runtime/phase1i/study-record.schema.json
```

该 Schema 只服务本次研究，不能复制到生产 API 或持久化层。每次修改研究记录后先校验：

```powershell
backend/.venv/Scripts/python.exe scripts/phase1i_quality_validation.py validate `
  runtime/phase1i/study.json
```

校验失败时不得继续生成、盲审或统计；先修复身份、Evidence、模型一致性、七天窗口或临时对象闭合问题。

## 4. 三次内容任务

### Cycle 1：当前聊天基线

老板直接使用当前空白聊天，不给操作员预先整理的档案，不改变现有前端或 API。保存最终 `title`、`script_text`、`shooting_notes` 和可用的 `ready_content_id` 到 `BaselineOutput`。

操作员可以解决登录、网络或复制等技术问题，但不能建议方向、补事实、改文案或替老板回答。基线未进入 READY 时，本次仍保留为未完成记录，不能伪造 `BaselineOutput`。

### Cycle 2、3：Concierge 目标流程

操作员先形成 `OwnerContextPacket`：

- `evidence_sources`：老板访谈、老板消息或老板明确确认的材料；
- `confirmed_facts`：只能写证据原意能够直接支持的门店事实；
- `voice_samples`：老板逐字原话，只用于理解表达方式；
- `recent_context`、`content_goal`、`available_scenes`、`constraints`：都必须有老板证据；
- `observed_preferences`：只记录之前 Cycle 的行为观察，状态固定为 `OBSERVATION_ONLY`；
- `operator_declaration`：确认未把推断升级为事实，也未添加文案。

AI 随后执行目标体验：

1. 主动推荐一个当前最值得拍的方向并说明原因，老板可以接受、替换或拒绝；
2. 只问一个当前最影响核心表达的问题，得到回答后再判断是否还需追问；
3. 只交付一条主稿，不用三份完整候选把判断责任还给老板；
4. 允许换 Hook、调语气、缩短时长、换画面和局部措辞；
5. 任何需要老板确认的事实必须排除出脚本，或列入 `pending_fact_risks`。

生成 `ShootReadyPackage` 前先计算上下文哈希：

```powershell
backend/.venv/Scripts/python.exe scripts/phase1i_quality_validation.py context-hash `
  runtime/phase1i/study.json --participant P001 --cycle 2
```

将输出写入 `owner_context_sha256`。工具会验证：

- 所有脚本引用都闭合到当前 `OwnerContextPacket` 的证据化对象；
- 分段顺序连续，拍摄顺序完整且无重复；
- 总时长为 20–60 秒；
- Package、Context、参与者和 Cycle 身份一致；
- 同一研究的所有 Cycle 使用同一模型。

## 5. 操作员纪律

允许的介入只有：

- `FACT_ORGANIZATION`：整理老板明确原话和证据引用；
- `QUESTION_RELAY`：原样传递 AI 问题；
- `RESPONSE_RELAY`：原样传递老板回答；
- `TECHNICAL_HELP`：处理不改变内容的技术问题。

操作员若改写方向、脚本或拍摄建议，必须记录为 `CONTENT_EDIT` 且 `content_changed=true`。该 Cycle 会保留用于问题复盘，但自动从门槛统计中排除。禁止为了让结果好看而漏记介入。

## 6. 反馈与事实审计

产物生成后，`recording_due_at` 必须精确等于生成时间后七天。回访记录：

- 方向是接受、替换还是拒绝；
- 产物是否接受；
- 最终是未修改、局部修改还是实质重写；
- 是否在七天内实际录制；
- 未录制原因属于 Direction、Material、Writing、Execution 或 Other；
- 老板原话反馈；
- 接受或录制稿中的未经确认事实及具体问题。

“局部修改”不得改变核心方向、主要事实或整体结构。任何事实纠正都必须如实记录；如果接受或录制稿含未经确认事实，事实硬门槛直接失败。

## 7. 专业编导盲审

研究负责人使用以下命令导出统一格式的盲审材料：

```powershell
backend/.venv/Scripts/python.exe scripts/phase1i_quality_validation.py blind-bundle `
  runtime/phase1i/study.json `
  --review-output runtime/phase1i/reviewer-bundle `
  --mapping-output runtime/phase1i/coordinator/blind-mapping.json
```

只把 `reviewer-bundle` 交给编导；`blind-mapping.json` 由研究负责人保管。统一材料不暴露参与者编号、Cycle 或 Baseline/Concierge 标签。

编导不得给固定权重或总分，只对五个维度给 `PASS`、`CONCERN` 或 `FAIL`，并写具体诊断：

- `authenticity`：是否像真实老板，而非通用营销话术；
- `clarity`：核心表达是否清楚且集中；
- `evidence_strength`：是否有具体事实或可见画面支撑；
- `watchability`：陌生人是否有继续观看的动力；
- `shootability`：门店当天是否现实可拍。

总体失败必须归因到 Direction、Material、Writing 或 Execution，不能只写“模型效果不好”。

## 8. 汇总与阶段决定

```powershell
backend/.venv/Scripts/python.exe scripts/phase1i_quality_validation.py summary `
  runtime/phase1i/study.json
```

需要机器可读结果时追加 `--json`。工具按已确认规则返回：

- `NOT_READY`：参与者、品类、完整 Cycle、事实审计或盲审尚未达到完整样本；
- `BLOCKED_FACT_BOUNDARY`：任何接受或录制稿出现未经确认事实；
- `ITERATE_PHASE_1I`：样本完整，但 Concierge 七天实际录制率不足三分之一，或未优于当前聊天基线；
- `READY_FOR_PHASE_1J`：事实硬门槛通过、10–20 个有效 Concierge Package 至少三分之一实际录制，并且录制率优于基线。

该汇总不代替根因复盘。进入 Phase 1J 前还必须阅读 `root_cause_diagnostics` 和盲审原文：

- Direction 为主：重做主动内容机会判断；
- Material 为主：优化渐进式最少追问；
- Writing 为主：调整 CREATE/REVIEW 创意契约和拍摄包；
- Execution 为主：改善镜头与拍摄任务设计。

在研究记录返回 `READY_FOR_PHASE_1J` 且人工复盘确认之前，不得设计或实现长期关系层、主动生产入口、正式拍摄卡片 Schema 或局部编辑生产契约。
