# REVIEW 与测试专项规则

仅在任务直接涉及 REVIEW 逻辑、Review 测试、验收、回归、质量诊断或验证策略时读取。

## REVIEW 诊断

先判断根因，不用固定总分或默认局部改写代替判断：

- Writing Problem：方向和真实素材足够，问题在表达、结构或成稿，回到 `CREATE`。
- Material Problem：方向可继续，但真实素材不足以支撑核心表达，回到 `DEEPEN`。
- Direction Problem：方向本身不值得继续或无法成立，回到 `EXPLORE`。

程序合规、真实性和结构检查可以提供保护边界，但不能改变 Owner Facts，也不能用热点或知识案例补成老板事实。旧 Director Review、评分字段和局部修稿能力只按 Legacy / 可复用实现处理，除非任务明确要求，不把它们当作新主线固定逻辑。

## 验证纪律

- 先运行与改动范围匹配的最窄检查，再运行受影响组件的完整检查；使用项目 `AGENTS.md` 规定的命令，不在 Skill 中复制完整测试清单。
- 新增或改变行为时补充相称的测试；外部付费 AI 调用在自动化测试中 mock，除非用户明确要求受控集成测试。
- 完成前检查 `git diff`、`git status --short`、兼容性影响和无关改动，并如实报告测试 / build / type / lint 结果。
- 测试通过不能替代重大产品、Schema、API、成本或验收决策的用户确认。
