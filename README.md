# Food-IP

Food-IP 是**餐饮老板的 AI 内容编导**。它帮助老板找到现在最值得说的内容，补足真正影响内容质量的真实素材，完成创作与根因自检，最终交付可拍内容。

面向用户的完整目标体验是：

```text
发现
→ 深挖
→ 判断
→ 定方向
→ 再深挖
→ 创作
→ 自检
→ 可拍
```

产品内部主状态统一为：

```text
EXPLORE
→ DEEPEN
→ CREATE
→ REVIEW
→ READY
```

Workflow 只控制关键边界与状态流转，AI 负责边界内的具体创作判断。`REVIEW` 必须先判断根因，再决定返回 `CREATE`、`DEEPEN` 或 `EXPLORE`，不能默认直接重写。

## 当前产品方向

- 旧 `ResearchProfile → IPProfile → CreativeBrief → TopicCard → ScriptBundle → 固定评分 Review` 创作主线已冻结为 **Legacy**，只承担兼容、基线和必要维护，不再作为新产品架构基础。
- 后续新建独立的 **Director Core**，以 `EXPLORE → DEEPEN → CREATE → REVIEW → READY` 为主链；当前仓库尚未把这套新内核描述为已实现。
- 现有 Materials、Timeline、FFmpeg、Export 与部分成熟持久化能力继续作为工程底座复用。`backend/app/engine/timeline.py` 仍是时间轴时长的权威来源。
- `knowledge_pipeline/` 保持独立的知识生产子系统，不等同于整个 Food-IP 产品主线，也不直接耦合进 Director Core。
- Owner Facts 只能来自老板或其他明确可信、已确认的来源；Knowledge 教 AI 如何判断，不能证明当前餐厅发生了什么。

当前开发规范以 [`AGENTS.md`](AGENTS.md) 与 [`.codex/skills/food-ip-engineer/SKILL.md`](.codex/skills/food-ip-engineer/SKILL.md) 为准。目标架构与下一阶段范围分别见 [`docs/architecture.md`](docs/architecture.md) 和 [`docs/next-tasks.md`](docs/next-tasks.md)。

## 仓库结构

```text
backend/             产品后端、Legacy 创作能力与可复用生产能力
frontend/            当前 Web 客户端
knowledge_pipeline/  独立 Creative Knowledge 生产子系统
docs/                架构、API、产品决策、部署与项目文档
runtime/             本地项目运行数据，不是源代码
```

## 快速开始

要求：Python 3.10+、Node.js/npm，以及可用的 ffmpeg/ffprobe。下列命令均从仓库根目录执行。

### 后端

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
backend/.venv/Scripts/python.exe -m uvicorn backend.app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/docs> 查看 OpenAPI 页面。若前端尚未构建，访问 `/` 会返回带构建提示的 JSON。

### 前端开发模式

```powershell
cd frontend
npm install
npm run dev
```

Vite 默认运行在 <http://localhost:5173>，开发服务器把 `/api` 代理到 <http://127.0.0.1:8000>。

### 单进程生产模式

```powershell
cd frontend
npm run build
cd ..
backend/.venv/Scripts/python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

构建后，FastAPI 从 `frontend/dist` 托管首页与 `/assets` 静态文件；API 仍位于 `/api`。

## 环境变量

可复制 `.env.example` 为仓库根目录的 `.env`。进程环境优先于 `.env`，多个 CORS 来源用英文逗号分隔。

| 变量 | 默认值/作用 |
|---|---|
| `CODEX_BIN` | 默认从 `PATH` 查找 `codex`；也可填写绝对路径，当前仅为后续可选脚本增强预留 |
| `PROJECTS_ROOT` | `<仓库根>/runtime/projects`；项目 JSON、素材、预览和导出的持久化目录 |
| `CORS_ORIGINS` | `http://localhost:5173`；允许跨域访问 API 的前端来源列表 |
| `FRONTEND_DIST` | `<仓库根>/frontend/dist`；可选覆盖生产前端构建目录 |
| `DEEPSEEK_API_KEY` | DeepSeek 密钥；只写入本机 `.env`，前端和接口响应不会返回该值 |
| `AI_SCRIPT_BASE_URL` | 默认 `https://api.deepseek.com`；Legacy 脚本生成兼容配置 |
| `AI_SCRIPT_MODEL` | 默认 `deepseek-v4-flash`；Legacy 脚本生成兼容配置 |
| `AI_SCRIPT_THINKING` | 默认 `disabled`；Legacy 脚本生成兼容配置 |
| `AI_SCRIPT_TIMEOUT_SECONDS` | 默认 `90`；Legacy 模型请求超时秒数 |

## 验证

后端：

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/app/tests -q --basetemp .pytest-basetemp
```

前端：

```powershell
cd frontend
npm.cmd run build
```

Knowledge Pipeline：

```powershell
cd knowledge_pipeline
python -m pytest -q
```

现有 REST API 行为见 [`docs/api.md`](docs/api.md)。[`docs/questionnaire-design.md`](docs/questionnaire-design.md) 仅记录旧调研与多脚本方案，不代表当前产品主线。
