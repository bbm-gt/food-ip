# Claude ↔ Codex 委托协议

**总则**：Claude Code（总指挥）定架构、写任务规格书、验收、迭代决策；Codex（执行者）写代码、搭工具链、修 bug。代码从第 0 步就在 git 里，所有 Codex 产出可 diff、可回滚。

## 委托命令速查

Codex CLI 路径（已登录）：
```
C:\Users\HP\AppData\Local\OpenAI\Codex\bin\d7e8094cfb76a267\codex.exe
```

### 只读任务（调研 / 评审，无副作用）
```
codex exec --skip-git-repo-check --ephemeral -s read-only ^
  -C "C:\Users\HP\food-ip" -o docs\tasks\NN-report.md ^
  "<任务描述，引用 docs/architecture.md 等>"
```

### 实现任务（写代码，锚定在仓库内）
```
codex exec --skip-git-repo-check --ephemeral -s workspace-write ^
  -C "C:\Users\HP\food-ip" -o docs\tasks\NN-report.md ^
  "阅读并执行 docs/tasks/NN-*.md。完成后运行验证并修复失败。只允许改规格书列出的目录。用中文汇报。"
```
- **`codex exec` 不接受 `--ask-for-approval`**（那是顶层参数，exec 子命令传了会报 `unexpected argument` 错误）。exec 非交互时 approval 默认 never，安全性靠 `-s` sandbox 控制。
- 实现任务用 `-s workspace-write`（写权限限仓库内）+ `-C` 钉目录；只读任务用 `-s read-only`。
- 默认模型 gpt-5.6-sol（高推理）；可用 `-m <model>` / `-c model_reasoning_effort=low` 覆盖。

## 任务规格书模板（docs/tasks/NN-*.md）

每个规格书固定包含：
1. **目标**：一句话说清要交付什么。
2. **技术上下文**：引用 docs/architecture.md 相关部分。
3. **步骤**：明确、可执行的步骤清单。
4. **验收标准**：可检验的硬指标。
5. **可改文件**：明确列出允许改动的目录/文件。
6. **禁止项**：如"不得删 docs/""不得改 API 契约（除非本任务声明）"。

## Claude 验收流程

1. `git diff --stat` + 读关键文件
2. 跑 `pytest` / `npx tsc --noEmit` / `npm run build`
3. 有运行时面则端到端驱动验证
4. 在 `docs/tasks/NN-report.md` 记录验收结果
5. 不通过 → 反馈 Codex 迭代（≤3 轮），超限 Claude 亲自接管

## 防幻觉（重要，强制）

codex 一次执行大任务时可能把"推测"当"已完成"。必须靠**任务拆分 + 证据 + 独立验收**压制：

1. **拆小任务**：一个大规格书拆成多个小任务，每个只做一件事、有可验证的验收标准。上下文小 → 幻觉少。
2. **验收靠证据不靠自述**：codex 汇报必须附**真实命令输出**（退出码、pytest 结果、build 日志、文件内容），禁止只给结论。
3. **总指挥独立复验**：不信任自报，自己跑 pytest / build / 调接口 / 看 git diff。测试通过 = 铁证，codex 无法幻觉出一个通过的 pytest。
4. **规格书防幻觉约束**（写进每个规格书）：不确定就说不确定；禁止伪造输出/测试结果；失败如实报告；只报告真实运行过的命令。
5. **git diff 逐行审查**：幻觉代码一 review 就露馅。
6. **迭代上限 ≤3 轮**，超限总指挥接管。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| codex exec 超时（模型延迟） | 后端统一 240s timeout + kill；委托实现类任务由 Claude 限时监控 |
| 模型不可用/鉴权失败 | exec 非零退出 → 解析 stderr → 中文提示重试 |
| 脚本不是纯 JSON | validate.py 剥围栏 + 括号提取 + Pydantic 校验 + 带错重试 1 次；`-o` 留痕 |
| 自动审批让 codex 乱跑命令 | `-s workspace-write` + `-C` 钉目录 + 规格书限定范围 + git diff 审查 |
