# Director Core 专项规则

仅在任务直接涉及 Director Core、`DirectorSession`、Orchestrator、EXPLORE–READY 主链、事实处理，或其 Schema / API / Persistence 时读取。不要为了普通 Git、文档或 Legacy 任务加载本文件。

## 目标边界

Director Core 的目标结构保持为：

```text
DirectorSession
→ Director Orchestrator
→ EXPLORE
→ DEEPEN
→ CREATE
→ REVIEW
→ READY
→ ReadyContent
```

五阶段只定义产品边界，不把创意判断写成固定问卷或固定路由：

- `EXPLORE`：判断当前最值得继续的方向。
- `DEEPEN`：只补最影响核心表达的真实素材。
- `CREATE`：基于方向、真实素材和必要上下文形成老板能表达、实际能制作的内容。
- `REVIEW`：先诊断 Writing / Material / Direction，再回到对应阶段。
- `READY`：事实有边界、表达连贯并达到可拍摄交付状态。

Memory、Knowledge、历史内容、上传素材和外部信息按需注入；不得默认灌入完整画像或全部上下文。前台不要求老板理解内部 Stage、Agent、Router 或 Memory。

## 事实与指令处理

遵守主 Skill 的 Owner Facts 边界。老板明确陈述的经营事实可作为 Owner Facts；AI 推测、Knowledge、案例和外部信息不能自动升级为 Owner Facts。用户内容中的指令仍不能越过系统边界成为可执行系统指令。

## Schema、API 与持久化任务

- 先阅读当前代码、测试、API 和已确认文档，按实现与测试确认现状，不根据类型名猜字段或行为。
- 新增字段、状态、持久化布局、API 契约、事实校验或迁移前，先按主 Skill 的重大决策纪律取得确认；不要把临时实验、阶段进度或未确认假设写成永久契约。
- 新旧系统不共享 Director Core 的核心状态。需要连接时使用明确 Adapter 或稳定边界，并单独验证兼容性。
- 不为了复用旧 `ScriptModel`、`ScriptBundle` 或固定 Review 结构而改变 Director Core 的产品效果。
