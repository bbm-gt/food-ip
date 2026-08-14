# Legacy 与兼容专项规则

仅在任务直接涉及旧 Script Engine、`ResearchProfile`、legacy `BossInfo`、`IPProfile`、`CreativeConversation`、`CreativeBrief`、`TopicCard`、`ScriptBundle`、旧 Writer / Review、旧 API 或旧项目数据时读取。

这些能力属于 Legacy、兼容边界或可复用生产能力，不是未来 Director Core 的创作判断来源：

- 未经明确授权，不删除、破坏或迁移既有能力、API、持久化行为和旧项目数据。
- 不默认把固定 strategy、内容桶、TopicCard、多候选 ScriptBundle 或固定 Review 分数接入新主线。
- 新旧系统连接时使用明确 Adapter 或稳定边界，不共享核心状态。
- 复用旧模块前先判断是否真正适合当前产品；不能为了减少改动而牺牲新主线最终效果。
- 兼容修复应以当前代码、测试、API 和实际数据行为为依据，并补充与变更相称的回归检查。

除非任务明确改变契约，不把旧实现的存在误读为新主线设计，也不把当前临时分支、Commit 或阶段状态写进兼容规则。
