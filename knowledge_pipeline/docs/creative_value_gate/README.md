# Phase 0.5 Creative Value Gate — A/B 对照评估档案

> 归档日期：2026-08-10
> 评估方式：evaluation-only A/B，一次性调用（未修改生产代码 / 未新增 Director / Creative Decision Schema / API / Retrieval / Knowledge Pipeline）
> 权威依据：`CLAUDE.md` §17.2、§18、§19

本档案记录 4 个真实 owner 场景下"不使用 5-video Knowledge（A 组） vs 使用高相关 5-video Knowledge（B 组）"的对照评估，用于后续独立审计。

## 1. 实验设置

- **模型**：`deepseek-v4-flash`，base `https://api.deepseek.com/v1/chat/completions`
- **参数**：temperature 0.3，max_tokens 8000，`"thinking": {"type": "disabled"}`
- **A/B 唯一变量**：B 组的 user prompt 额外追加真实 Knowledge block；system prompt、模型、参数、输出格式两组完全相同
- **输出格式**：11 个临时评估字段（非正式生产 Schema）：`objective / audience_value / core_material / angle / core_tension / proof / information_flow / business_role / performer_fit / missing_facts / risk_flags`
- **统一事实边界规则**（system prompt 内，A/B 相同）：只把 owner task 明确提供的经营事实当已知事实；限量/卖完/排队/顾客反馈/销量/优惠/赠品/产地/品质证明/价格/客流等未提供信息不得写成确定事实；可能提升创作时放入 `missing_facts` 标"需确认"或写成条件性建议；不得为增强张力自补经营事实。

### 1.1 Knowledge 注入规则

- Knowledge 必须逐字取自真实产物：`E:\food_ip_knowledge\atomic\knowledge_cards.jsonl`（57 张卡）与 `E:\food_ip_knowledge\synthesis\questions.jsonl`（7 条综合答案）。
- 按场景选**高相关**产物，不塞全部 57 条；卡片按 `knowledge_id` 选取（存在同名卡片，禁止按 title 查）。
- 课程案例卡（`case_cards.jsonl`）一律**不注入**，防止案例错误迁移。

### 1.2 判定尺度

| 判定 | 条件 |
|---|---|
| PASS | Knowledge 带来明确且可归因的决策增益，且无重要副作用 |
| PARTIAL | 存在真实增益，但增益有限 / 不稳定，或伴随明显问题 |
| FAIL | 无明显增益，或发生硬套 / 错误迁移 / 事实污染 |

## 2. 场景与产物

每个场景的完整证据（A/B user prompt、raw 输出、parsed JSON、完整 Knowledge block、选取 ID、选择理由）见 `scene{1,2,3,4}_ab_output.json`；可复跑脚本见 `eval_creative_ab.py`（场景 1）、`eval_scenes_234.py`（场景 2–4）。

### SCENE 1 — 新到特大生蚝（即时到店）
owner task：今天店里刚到一批特别大的生蚝，老板想拍条视频，目标是吸引附近顾客到店。

- B 注入：`Q004/Q011/Q020/Q031/Q032` + 8 张卡（`KID_804c54049794` 低客单转化逻辑、`KID_a81ee8152922` 营销门店型定位、`KID_1c9baa33e30a` 确定性爆点、`KID_7444a08fd87d` 只提概率、`KID_a7e2d5201fc5` 开头钩子、`KID_088aea86ce2b` 少拍套餐拍老板视角、`KID_7b1329e45481` 避谈价格、`KID_cdafa6463b56` 新鲜是核心）
- 结果：**PARTIAL / Strong Positive Signal**。B 在 angle / information_flow / business_role / missing_facts 上实质增益，结构化为"确定性爆点 + 门店引流型定位 + 避谈价格"；但单场景不足以正式通过，且 A/B 两组都出现"把未确认事实（限量）直接写成创作事实"的问题（该问题在后续场景中由统一事实边界规则约束）。

### SCENE 2 — 无天然爆点（普通营业日）
owner task：今天店里没有新品、没有活动、没有特别事件，就是普通营业的一天。老板想拍一条视频，让附近顾客记住这家店，并愿意近期来吃。

- B 注入：`Q010/Q011/Q013` + 8 张卡（`KID_cc64eb57a60b` 用心经营是选题源头、`KID_290622697584` 客流视频=记录真实善行、`KID_4aed84e909fe` 真做是持续产出的根基、`KID_36374ba42d4d` 从瞎拍到创作的朋友圈、`KID_9763aabedc73` 持续客流内容公式、`KID_e9ae2d8f2a61` 真做是IP内容的前提、`KID_af9bbd7583e3` 服务用心是营销门店型内容、`KID_1c9baa33e30a` 确定性爆点原则【压力项】）
- 逐字段：B 在 **proof / missing_facts** 实质增益（服务细节可拍 + 开拍前确认真实的"为顾客好的小事"）；objective / audience_value / core_tension / information_flow / business_role 基本相同；risk_flags 轻微精简（删了 A 的"不虚构排队/好评""顾客隐私"两点）。
- 检查：压力项"确定性爆点原则"未诱发编造；但独立复核仍发现未确认事实（如"为顾客好的小事"等服务细节）被直接写入 `proof / information_flow` 等字段，存在事实越界；B 实际更短（813 vs 871 字符），增益与长度无关。
- 判定：**PARTIAL**

### SCENE 3 — 价格与长期定位边界
owner task：店里准备上一个 99 元双人套餐，老板想拍一条视频促进附近顾客到店，但不希望账号长期变成只靠低价促销吸引人的账号。

- B 注入：`Q004/Q031/Q020` + 8 张卡，含**两对冲突项**：`KID_40533b0cab50` 套餐可拍 vs `KID_088aea86ce2b` 少拍套餐拍老板视角；`KID_7b1329e45481` 避谈价格 vs `KID_001d7861fcb2` 套餐核心在价格；另含 `KID_fddd06ac9852` 内容定调决定用户质量、`KID_d2896ad76630` 经营目标定内容方向、`KID_5cdd1ca48bb8` 低消费人群=薅羊毛人群
- 逐字段：B 在 **performer_fit / risk_flags** 实质增益——条件化应用"避谈价格"（价格可作钩子但避免"便宜/超值"导向词、防薅羊毛），与注入的 anti_pattern 一致（A 的 core_tension 仍出现"便宜"一词）；未机械执行"不讲价格"、未出现"低价=不能拍"；调和了冲突对。
- 检查：A 已独立掌握"价格是钩子 + 避免长期变促销号"（A 的 risk4），故本场景增益属**精修型**，边际最小；但独立复核仍发现未确认事实（套餐 / 价格相关细节）被直接写入 `core_material / information_flow` 等字段，存在事实越界；长度几乎相等。
- 判定：**PARTIAL（边际增益最小的一组）**

### SCENE 4 — 长期信任型老板内容
owner task：老板每天早上亲自去市场挑海鲜，他想拍一条视频。目标不是今天立刻卖多少，而是让附近顾客慢慢觉得这个老板懂海鲜、选货认真、值得信任。

- B 注入：`Q031/Q020/Q010` + 7 张卡（`KID_6f44ea065496` 人设IP讲故事与门店卖点、`KID_cb12a3f2c9e3` 人设故事驱动复购、`KID_6e36d9dbda74` IP吸引高质量用户、`KID_cc64eb57a60b` 用心经营是选题源头、`KID_290622697584` 客流视频=记录真实善行、`KID_36374ba42d4d` 从瞎拍到创作的朋友圈、`KID_cdafa6463b56` 新鲜是营销门店型核心【压力项】）
- 逐字段：B 在 **core_tension / proof / risk_flags / information_flow** 实质增益——对比式证据（放弃劣货、翻蟹脐、两批鱼鳃对比）、"真实性优先"风险（老板若不真每天挑货视频反而损害信任）、带秒数的三段式流程；A 已独立识别信任 objective、不强加优惠/限量/CTA。
- 检查：关键正面发现——注入的 Q020（低客单→一条视频给出到店理由）**压力项未把 B 拉向即时转化**，B 保持信任 objective，仅采用其"高频复购→长期价值"侧面，体现适用条件约束力；但独立复核确认本场景事实越界最典型——"翻蟹脐""两批鱼鳃对比"等挑货动作是老板未提供的事实，被直接写成 `proof`，必须标"需确认"或"如果事实成立，可以这样拍"；B 更长（1414 vs 1095 字符）但为结构性增量（时间轴机位、编号风险、证据清单），判定为质量性增益。
- 判定：**PARTIAL / Strong Positive**

## 3. 合并判定

| 标准 | 结果 |
|---|---|
| 3 个新增场景中至少 2 个明确 Knowledge 增益 | 满足（3/3 均有可归因增益） |
| Knowledge 能适应不同 business objective | 满足（记忆/近期到店、即时转化+长期定位、纯长期信任） |
| 无严重 Knowledge 硬套 | 满足（三处压力项均未被强制套用） |
| 无课程案例错误迁移 | 满足（案例卡未注入，输出无课程案例事实） |
| Knowledge 不新增虚假经营事实 | **未满足（当前 blocker = Fact Boundary）**：独立复核确认未确认事实仍被直接写入 `core_material / proof / information_flow` 等字段 |
| 能处理适用条件而非机械执行 | 满足（SCENE 3 调和冲突对、SCENE 4 拒绝 Q020 即时转化牵引） |
| 增益来自决策质量而非回答长度 | 满足（SCENE 2 B 更短、SCENE 3 等长、SCENE 4 结构性更长） |
| 增益可追溯到实际注入的 Knowledge | 满足（每处增益对应具体 QSID / knowledge_id） |

**Phase 0.5 Creative Value Gate = PARTIAL / STRONG POSITIVE**（非 PASS；SCENE 1 = PARTIAL / Strong Positive，SCENE 2 = PARTIAL，SCENE 3 = PARTIAL，SCENE 4 = PARTIAL / Strong Positive；独立复核确认 Creative Decision 仍存在事实越界）

### 必须如实记录的三点

1. **增益边际依场景差异大**：SCENE 1/2 为决策塑造型，SCENE 3/4 为精修型（A 已基本正确）。这反向印证 `CLAUDE.md` §18——只有高相关检索才值得注入，塞全部知识几乎必然稀释。
2. **事实边界合规主要由统一 SYSTEM 规则保证，不是 Knowledge**：SCENE 1 两组都曾把"限量"写成事实；SCENE 2–4 加了共享事实边界规则后，独立复核仍发现未确认事实被直接写入 Creative Decision 字段。边界必须在 system/validation 层强制，不能指望知识本身提供，也不能仅靠 prompt 规则根治。
3. **样本仍有限（4 个场景）**：结果为 PARTIAL / STRONG POSITIVE，**不授权** Creative Quality Benchmark V1 / 77-video 全量摄入 / Content Engine V2 / Director / 多智能体工程。下一阶段方向为 **Minimal Creative Decision / Fact Boundary + Minimal Retrieval Validation**。

### 复核修正（2026-08-11）

- 各场景最终判定：**SCENE 1 = PARTIAL / Strong Positive；SCENE 2 = PARTIAL；SCENE 3 = PARTIAL；SCENE 4 = PARTIAL / Strong Positive**。
- Scene 1–4 独立复核确认：Knowledge 已证明能改善 Creative Decision（Strong Positive Signal），**但 Creative Decision 仍存在事实越界**——AI 会把未确认的经营事实（如翻蟹脐、按虾、和摊主熟识等具体动作）直接写成创作事实。Scene 2–4 均存在不同程度的未确认事实被直接写入 `core_material / proof / information_flow` 等字段。
- 因此综合判定从 PASS 修正为 **PARTIAL / STRONG POSITIVE**。当前主 blocker = **Fact Boundary（事实边界）**。
- 事实边界修正要点：严格区分 `confirmed_facts`（老板明确提供/可信 Memory 已确认）、`creative_decision`（AI 创作判断）、`missing_facts`（需确认或条件化建议）。Knowledge 只教"怎么判断"，不充当"老板实际发生了什么"。边界必须由 system / validation 层强制，不能指望知识本身提供。
