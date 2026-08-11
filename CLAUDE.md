# Food-IP — Claude Code Instructions

本仓库当前项目规则、架构方向、工程约束和完成标准，
统一以根目录 `AGENTS.md` 为主要入口。

开始任务前请依次阅读：

1. `AGENTS.md`
2. `README.md`
3. `docs/architecture.md`
4. 与当前任务直接相关的代码和测试

不要依赖本文件维护独立的架构或产品规则，
避免与 `AGENTS.md` 产生双重事实源。

关键原则：

- 不推倒重写现有 Food-IP
- Legacy Script Generation 保留为 compatibility + baseline + reusable capabilities
- 新主链以 Intent → Facts → Retrieval → Creative Decision 为核心
- Knowledge 只教 AI 怎么判断，不能创造老板事实
- 不构建不必要的 Multi-Agent
- 架构、Schema、数据契约、路线、成本、验证标准等重大改变，先提出方案并等待用户确认
- 修改前执行 `git status --short`
- 不覆盖无关本地修改
- 完成前运行相关测试并检查最终 diff

如果本文件与 `AGENTS.md` 或当前代码/测试冲突，
优先遵循 `AGENTS.md`，并报告冲突。
