# Knowledge System 专项规则

仅在任务直接涉及 `knowledge_pipeline/`、知识摄入、证据与 provenance、快照、幂等、崩溃恢复或 Knowledge 可靠性时读取。

开始前先阅读 `knowledge_pipeline/AGENTS.md`，再按任务需要读取该子系统的相关代码、测试和文档。该子系统与产品运行时逻辑分离；不要把摄入内部实现直接耦合进产品运行时，也不要未经确认设计新的稳定合约、Persistence 或 Retrieval。

必须继续遵守主 Skill 的 Owner Facts / Knowledge 边界：Knowledge 教 AI 如何判断，不能告诉 AI 当前老板实际上发生了什么。不要把知识案例、外部信息或模型推断写成当前餐厅事实。

对已验证的摄入能力，除非出现具体回归、测试失败、不变量破坏或用户明确要求，不重新打开可靠性工作。修改时保护：

- 时间戳权威性；
- 稳定、确定性的身份；
- evidence / provenance；
- 严格 Schema 校验；
- crash / resume；
- 幂等重跑；
- 按来源持久化；
- 原子全局快照；
- fail-fast 校验。

尚未确认的知识源范围、分层、准入、证据标准、时效治理、评分、Retrieval 和评估方案，不得写成既定设计。具体实现与测试规则以 `knowledge_pipeline/AGENTS.md` 为准。
