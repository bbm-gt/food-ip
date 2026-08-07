---
name: food-ip-engineer
description: 用于维护 Food IP Studio 项目的前后端、AI 脚本生成、素材处理、时间轴编辑和 FFmpeg 导出流程。适用于分析、修改、调试或扩展该项目时，尤其适用于需要遵守既有 API、文件持久化、AI 质量约束和视频时间轴规则的任务。
---

# 项目定位

Food IP Studio 是面向餐饮老板的 AI 短视频 IP 生产系统。

核心创作流程（实体级，当前真实流程）：

ResearchProfile（深度调研）
→ IPProfile（IP定位）
→ CreativeConversation（AI编导共创）
→ CreativeBrief 确认
→ TopicCard（选题锁题）
→ ScriptBundle（多脚本候选）
→ 用户选择候选
→ script.json（当前脚本）

之后进入：素材上传 → 时间轴编辑 → FFmpeg 导出。

# 技术架构

Backend:

- FastAPI
- Python
- 文件夹持久化
- DeepSeek AI生成
- FFmpeg视频处理

Frontend:

- React
- TypeScript
- Vite

# 选题与脚本生成规则（TopicCard / ScriptBundle）

## TopicCard 锁题

- TopicCard 是 CreativeBrief 与详细脚本之间的选题层。
- 用户一旦选择 TopicCard，后续详细脚本必须锁定该 TopicCard 的核心主题。
- 三套候选脚本必须围绕同一个 TopicCard。
- 三套脚本的差异应来自不同表达方式、叙事结构、Hook、证据展示方式或拍摄方式，而不是来自不同主题。
- 不允许底层通用 strategy 再把三套候选重新变成三个不同主题。

## 跳过 TopicCard 的例外

- 若用户没有选择 TopicCard，而是明确选择「跳过 TopicCard，直接从 confirmed Brief 生成」，可以继续使用现有 strategy 推荐逻辑（不同 strategy 可各自成题）。

## ScriptBundle 数据边界

- 生成 ScriptBundle 只产出候选，不写入当前脚本。
- ScriptBundle 不得自动覆盖当前 script.json。
- 只有用户明确选择某个候选后，才将该脚本保存为当前 script.json。
- 保留现有 script version 行为（当前脚本实际变化时向 script_versions.json 追加快照）。

# AI 脚本质量规则（编剧 / 编导职责分离）

## 1. AI 脚本质量流程原则

推荐流程：

脚本生成
→ 程序硬规则校验
→ AI 编导审稿
→ 对低质量部分进行局部修稿
→ 再次程序校验
→ 输出最终候选

## 2. AI 编剧与 AI 编导职责分离

AI 编剧负责：

- 根据已确认事实、IP、Brief、TopicCard 生成脚本。
- 负责创意表达和镜头设计。

AI 编导负责：

- 不负责重新选题。
- 不擅自增加新经营事实。
- 对已生成脚本进行独立质量评价。
- 找出具体低质量位置并给出修改建议。

底层可以暂时使用同一个模型，但 system prompt、输入输出 Schema 和职责必须分开。

## 3. 编导评价维度

至少评价以下维度：

- opening_hook_strength：开头吸引力
- oral_naturalness：老板说话是否自然、是否存在 AI 腔
- information_density：是否有废话或重复表达
- progression：内容是否持续推进、有没有继续观看动力
- evidence_strength：核心观点是否有真实证据支撑
- ip_alignment：是否符合老板 IP
- shootability：普通餐饮老板能否实际完成拍摄
- ad_feeling：是否过于像硬广告
- distinctiveness：三套候选之间是否有足够差异

## 4. 修稿原则

- 优先局部修稿，不要因为一个镜头有问题就整篇重新生成。
- 编导必须指出具体问题位置，而不是只给一个总分。
- 修稿不得改变已确认事实。
- 修稿不得偏离 confirmed Brief 或 selected TopicCard。
- 修稿不得偷偷改变 IP 定位。
- 修稿后仍必须经过现有程序硬规则校验。

## 5. 编导评分 ≠ 发布审核

编导审稿是「内容质量优化层」；现有真实性、敏感词、拍摄限制等程序检查继续存在。

- 程序规则负责：不能出错、不能违规、不能编事实。
- AI 编导负责：脚本好不好看、像不像真人说话、有没有网感、节奏和表达是否够强。

两者职责不同，编导评分不替代程序合规检查。

## 6. 用户确认边界

可自动执行（内部）：

- AI 编导评分
- 局部自动修稿
- 再次质检

不得自动执行：

- 修改长期档案
- 修改已确认事实
- 修改 selected TopicCard
- 自动替用户选择最终候选脚本

# Creative 路径失败降级原则（fallback）

## 1. 模板 fallback 的适用范围

普通旧 AI 生成入口可以继续使用现有 template fallback。

## 2. 上下文驱动路径禁止静默降级

以下上下文驱动路径：

- IPProfile
- confirmed CreativeBrief
- selected TopicCard

如果 AI 生成失败，不允许直接静默退回完全不使用这些上下文的旧模板。

## 3. Creative 路径 fallback 的约束

- 保留 IPProfile。
- 保留 confirmed CreativeBrief。
- 如果已选择 TopicCard，必须继续锁定该 TopicCard。
- 不得重新换题。
- 不得丢失已确认事实。
- 不得凭空补充经营事实。

## 4. 推荐失败顺序

AI 正常生成
→ 修复 / 重试
→ 仍失败
→ 使用当前 Creative Context 做简化安全生成
→ 如果无法安全生成，则明确返回错误

不要为了「永远返回三套脚本」而生成与用户选题无关的内容。

## 5. 目标

fallback 的目标是「降低生成复杂度」，不是「丢弃上下文」。

# 开发原则

1. 项目事实来源优先级（从高到低，详见「任务执行协议」第 1 节）：

   - 当前实际代码
   - 自动化测试
   - `AGENTS.md`
   - `docs/architecture.md`
   - `docs/next-tasks.md`
   - `docs/api.md`
   - `README.md`
   - `HANDOFF.md` / `CLAUDE.md`（完成重写前仅作历史参考，不作权威来源）

   文档与当前代码或测试冲突时，一律以代码和测试为准。

2. 不允许破坏已有流程：

   - 项目创建
   - 调研
   - IP确认
   - AI共创
   - 脚本
   - 素材
   - 剪辑
   - 导出

3. 所有新增功能：

   - 优先小范围修改
   - 保持 API 兼容
   - 补充测试

4. 前端修改：

   注意 `frontend/src/App.tsx` 目前承担：

   - 页面流程状态
   - 当前项目状态
   - 刷新恢复逻辑

   不要随意重构。

5. AI相关：

   - AI生成内容必须经过程序约束。
   - 不要让模型直接覆盖业务数据。

6. 视频相关：

   保持：

   - `shot` 编号
   - `timeline` 权威计算
   - FFmpeg 导出流程

7. 执行任务时：

   - 先分析现状
   - 说明方案
   - 再修改代码

   不要直接重构。

## Project-specific references

No bundled scripts, references, or assets are required. Read the project files listed above directly when a task needs them.
# Task Execution Protocol

所有开发任务必须遵循以下流程。

## 1. 任务开始前

执行任务前：

- 检查 git status，确认当前工作区状态。
- 不覆盖用户已有修改。
- 阅读任务涉及的代码和文档。
- 不根据任务名称直接猜测实现方式。

按项目事实来源优先级阅读（从高到低）：

- 当前实际代码
- 自动化测试
- AGENTS.md
- docs/architecture.md
- docs/next-tasks.md
- docs/api.md
- README.md
- HANDOFF.md / CLAUDE.md（完成重写前仅作历史参考）

文档与当前代码或测试冲突时，以代码和测试为准；不把 HANDOFF.md 无条件当作权威来源。


## 2. 修改前分析

开始编码前，先分析：

- 当前代码实现方式
- 涉及模块
- 数据流变化
- 前后端影响范围
- 可能风险


输出：

1. 当前实现
2. 修改方案
3. 风险点


确认方案合理后再修改代码。


## 3. 编码原则

所有修改：

- 优先小范围修改。
- 不进行无关重构。
- 保持已有 API 兼容。
- 保持旧项目数据可读取。
- 新增能力必须补充测试。


禁止：

- 删除已有功能。
- 修改不相关模块。
- 改变核心数据结构而不说明。


## 4. 前端修改规则

修改 React 前端时：

注意当前 App.tsx 负责：

- 页面流程控制
- 当前项目状态
- 刷新恢复逻辑


修改前必须确认：

- 是否影响页面恢复。
- 是否影响项目上下文。
- 是否需要拆分组件。


避免：

- 随意增加全局状态。
- 在组件内复制业务状态。


## 5. AI功能修改规则

AI生成内容：

必须经过业务约束。

禁止：

- 直接信任模型输出。
- 覆盖真实业务数据。
- 自动生成未经确认的事实。


AI输出需要：

- 结构化解析。
- 程序校验。
- 用户确认。


## 6. 视频流程规则

视频相关修改：

保持：

- shot编号一致。
- timeline.py作为时间权威。
- FFmpeg导出流程。


禁止：

- 前端自行计算最终时长。
- 多处维护时间逻辑。


## 7. 完成任务后

必须执行：

后端：

```bash
pytest backend/app/tests -q
