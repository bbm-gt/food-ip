# Food-IP Director Core Architecture Amendment 003 — Current Facts and Semantic Review

## 1. 修订范围与权威关系

本 Amendment 定义 Script Core 对老板自然语言事实更正和 REVIEW 事实审核的最终规则。它明确修订 Core Architecture、Minimal SQLite Schema 与 Execution Contract 中要求把被纠正 Owner Fact 写入 `rejected_items`、并由新 Fact 记录 `supersedes_item_ids` 的条款；其他 Evidence、成功 Turn 原子性、幂等、恢复、Session 生命周期和 `ReadyContent` 契约不变。

正式文档内部顺序为：Core Architecture、Minimal SQLite Schema、Architecture Amendment 001、Execution Contract、Architecture Amendment 002、Architecture Amendment 003。若条款冲突，仅以本 Amendment 明确修订的事实更正与语义审核条款为准。

## 2. 自然语言事实更正

- 大模型负责理解老板自然表达中的确认、否定和更正，不把关键词、固定句式或程序问答树作为事实语义的核心判断。
- 例如老板说“不是凌晨四点熬汤，是早上六点”，当前事实只能是“早上六点熬汤”；被否定内容不能因出现在老板原话中而成为 Owner Fact。
- 当前 Session 已存在被纠正 Fact 时，下一版 Working State 直接从活跃 `owner_facts` 移除旧 Fact，并加入由当前 OWNER Message Evidence 支撑的新 Fact。
- 该更正不新增 `RejectedItem`，新 Fact 的 `supersedes_item_ids` 保持为空；后台不为这次更正额外建立隐藏的纠正记录。
- Raw Transcript 和已提交 Turn 仍按既有追加、幂等和恢复合同保存老板与 Director 的可见消息；它们不是额外的隐藏纠正台账，也不得把旧陈述重新注入当前事实。
- 为兼容既有数据，`rejected_items` 与 `supersedes_item_ids` 字段继续存在，历史记录不迁移、不删除；本 Amendment 不改变 Direction、AI Judgment、Legacy 或历史持久化对象的既有兼容读取。

## 3. REVIEW 语义事实审核

- REVIEW 必须同时看到当前活跃 Owner Facts、Owner Constraints、Unconfirmed Inferences、当前 Draft，以及按需提供且与 Owner Facts 明确分区的 Knowledge。
- 大模型按语义判断稿件中的具体餐厅或经营陈述是否由当前 Owner Facts 支撑；不得把“文字是否原样出现”作为唯一判断，也不得要求老板可见或后台持久化逐句 Claim Ledger。
- Knowledge、案例和创作方法只能指导选题、结构、表达与审核判断，不能证明当前老板或餐厅发生了什么。
- 若稿件包含与当前事实冲突、只由未确认推断支撑或没有 Owner Fact 支撑的具体陈述，REVIEW 必须返回 Material Problem，并按既有合同 `NEED_MATERIAL → DEEPEN`；只有事实边界与其他质量要求均通过时才可 READY。
- 该审核复用既有 REVIEW Stage 与既有模型调用，不新增 Workflow 节点、Agent、固定评分、字符串相似度 Router 或额外模型调用。

## 4. 验证要求

- 自动化回归至少覆盖自然语言否定加更正、已有事实直接替换、无隐藏 Rejected Fact，以及同义改写和数字写法变化下的 REVIEW 路由。
- 受控真实模型验证必须同时检查：被否定事实未进入当前 Owner Facts；与当前事实冲突或无事实支撑的改写稿不能 READY。
- 字面匹配只能保留为明显错误的防御性兜底，不能替代 REVIEW 获得完整事实语境后的语义判断。
