# food-ip 维护指南

本仓库是已有的 Food IP Studio 项目；维护时以代码、测试和本文档为准，不从零搭建或重构既有流程。

## 开始工作

修改前依次阅读根目录 `AGENTS.md`、`README.md`、`HANDOFF.md`、`docs/architecture.md`、`docs/api.md`，再阅读本任务涉及的前后端代码及测试。先执行 `git status --short`，保护工作区已有修改，不覆盖无关内容。

后端测试与前端构建（从仓库根目录）:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/app/tests -q --basetemp .pytest-basetemp
cd frontend
npm.cmd run build
```

在某些 Windows 环境中，默认 pytest 临时目录清理会触发权限错误；使用仓库内 `--basetemp` 可避免该环境问题。PowerShell 若拦截 `npm.ps1`，使用 `npm.cmd`。启动后端：

```powershell
backend/.venv/Scripts/python.exe -m uvicorn backend.app.main:app --reload --port 8000
```

前端开发：`cd frontend; npm.cmd run dev`。生产模式先构建前端，FastAPI 会托管 `frontend/dist`，API 保持 `/api` 前缀。

## 目录与架构

```text
backend/app/
  api/          REST 路由：项目、脚本、素材、编辑、导出、润色
  core/store.py 文件夹持久化和兼容读取
  scriptgen/    调研模型、规则选题、DeepSeek 生成、本地质检、AI 编导审稿与局部修稿
  engine/       时间轴、媒体探测、接缝预览、FFmpeg 成片导出
  polish/       AI 视频润色契约及尚未配置的 null provider
  tests/        pytest 测试
frontend/src/   Vite + React + TypeScript 客户端
docs/           架构、REST 合约与部署说明
runtime/projects/<id>/
               项目持久化：project.json、script.json、script_bundle.json、shots/、work/、exports/
```

产品既有流程必须保留：创建项目 → 深度调研 → 脚本选择 → 素材上传 → 时间轴/接缝编辑 → FFmpeg 导出。

## 当前真实状态

- 项目采用**文件夹持久化**，每个项目的数据位于 `runtime/projects/<id>/`；读写集中在 `backend/app/core/store.py`。
- 当前 AI 脚本主路径是**规则选题 + DeepSeek 结构化生成 + 程序质检**，入口为 `POST /api/projects/{id}/script-bundles/ai`。规则方案接口仍保留，供离线或兼容场景使用。
- AI 编导审稿：AI 路径生成并通过程序硬校验后自动附加可选 `review`（9 维 1-10 评分）与 `review_error`；编导只读，不重新选题、不修改事实 / IP / Brief / TopicCard。审稿失败不丢弃候选，仅记录 `review_error` 与 warning。
- TopicCard 锁题：选中 TopicCard 后三套候选必须锁定同一主题；锁题模式下 `strategy` 只是表现角度。程序化低分判定 `review.judge_revision_needed` 与 AI 局部修稿 `review.revise_script_candidate` 已实现（补丁式只修 issues 指定位置、锁题不可换题、修稿后复用现有硬规则校验）；**自动根据低分触发修稿尚未接入生成流程**。
- `scriptgen/generators/template.py` 和旧 `POST /script/template` 是旧单脚本兼容入口；`scriptgen/generators/codex.py` 只是会抛出 `NotImplementedError` 的兼容占位，不可当作生产生成链路。
- AI 视频润色尚未实现：当前仅 `null` provider，接口返回 `not_configured`。不要把该能力描述为已接入或实现真实模型调用。
- 成片时长的唯一权威来源是 `backend/app/engine/timeline.py` 的 `compute_timeline()`。UI、预览、接缝和导出不得各自计算或覆盖总时长。

## 兼容与禁止事项

- 保持已有 REST 接口兼容；升级时新增接口，不删除或改写旧接口语义。
- 保持旧项目及其 `project.json`、`script.json`、`script_bundle.json` 可打开；`ResearchProfile` 与旧 `BossInfo` 必须双向兼容。
- 不修改 `engine/timeline.py` 的权威职责，不改为前端或 FFmpeg 输出反推时长。
- 不删除已有项目创建、调研、脚本、上传、时间轴、接缝或导出能力；不做任务无关的大规模重构。
- 不擅自删除文件、提交、推送，或修改密钥与 `.env`。业务变更必须补充测试。

完成任务前运行相关 pytest 与 `npm.cmd run build`，检查 `git diff`，并说明修改文件、验证结果、兼容处理和剩余风险。
