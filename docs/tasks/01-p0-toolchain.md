# 任务 01：P0 骨架 + 工具链

## 目标
在 `C:\Users\HP\food-ip`（当前工作目录）建立项目骨架与完整工具链，使后端/前端能启动、Codex CLI 连通可用。

## 技术上下文
参考 `docs/architecture.md` 的「目录结构」「REST API」「技术栈」。环境：Windows 11，Python 3.10.5，Node v24.14.1，npm 11.11.0。系统**未安装 ffmpeg**，用 `imageio-ffmpeg` 的静态二进制。

## 步骤

### 1. Git 初始化
- `git init`（若未初始化）。
- 创建 `.gitignore`：`node_modules/`、`.venv/`、`__pycache__/`、`*.pyc`、`runtime/`、`.env`、`.vite/`、`dist/`。

### 2. 后端骨架（backend/）
- `backend/.venv`：`python -m venv .venv`。Windows 下解释器路径 `backend\.venv\Scripts\python.exe`。
- `backend/requirements.txt`：
  ```
  fastapi
  uvicorn[standard]
  imageio-ffmpeg
  pydantic
  python-multipart
  httpx
  pytest
  ```
- `backend/app/config.py`：读环境变量（支持 `.env`，可不用额外库，用 `os.environ` 带默认值即可）：
  - `CODEX_BIN`（默认 `C:\Users\HP\AppData\Local\OpenAI\Codex\bin\d7e8094cfb76a267\codex.exe`）
  - `PROJECTS_ROOT`（默认 `<仓库根>/runtime/projects`）
  - `CORS_ORIGINS`（默认 `http://localhost:5173`）
  - ffmpeg/ffprobe 定位：用 `imageio_ffmpeg.get_ffmpeg_exe()` / `get_ffprobe_exe()`，try/except，失败则 None。
- `backend/app/main.py`：FastAPI 应用。
  - `GET /api/health` → `{"ok": true, "ffmpeg": "<path|null>", "ffprobe": "<path|null>", "codex_bin": "<path>", "projects_root": "<path>"}`。
  - 挂 CORS 中间件（allow_origins 来自 config）。
  - 根路径 `/` 返回简单 JSON 说明即可（静态托管前端属 P4）。
- `.env.example`：复制 config 的默认值作为模板。

### 3. 前端骨架（frontend/）
- 用 `npm create vite@latest frontend -- --template react-ts` 创建。若该命令有交互提示阻塞，则**手动脚手架**等价结构：
  - `frontend/package.json`（react、react-dom、vite、@vitejs/plugin-react、typescript、@types/react、@types/react-dom）
  - `frontend/vite.config.ts`：`server.proxy` 把 `/api` 转发到 `http://127.0.0.1:8000`。
  - `frontend/tsconfig.json`、`frontend/index.html`、`frontend/src/main.tsx`、`frontend/src/App.tsx`。
- `App.tsx` 简化：显示「food-ip 正在初始化…」。
- `npm install`。

### 4. Codex CLI 连通性测试
- 运行：
  ```
  "C:\Users\HP\AppData\Local\OpenAI\Codex\bin\d7e8094cfb76a267\codex.exe" exec --skip-git-repo-check --ephemeral -s read-only -C "C:\Users\HP\food-ip" -o docs\tasks\01-probe-output.txt "Reply with exactly the JSON object: {\"pong\": true}"
  ```
- 确认 `docs/tasks/01-probe-output.txt` 内容含 `"pong": true`。

### 5. 验证
- 后端：`backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt`（如未装）。启动 uvicorn 或直接调用，确认 `/api/health` 返回 `ok: true` 且 ffmpeg/ffprobe 路径非 null。
- 前端：`cd frontend && npm run build` 通过（产出 dist/）。
- 写一个最小 `backend/app/tests/test_health.py`：用 `fastapi.testclient` 断言 `/api/health` 返回 200 且 `ok is True`、ffmpeg 路径非空。并让 `pytest` 能跑通。

## 验收标准
1. `backend\.venv` 存在；`/api/health` 返回 `ok=true` 且 `ffmpeg`/`ffprobe` 路径存在（imageio 探测成功）。
2. `frontend` 的 `npm run build` 通过。
3. Codex 连通测试输出含 `{"pong": true}`。
4. 仓库已 `git init`，有 `.gitignore`，`pytest` 通过（至少 test_health）。

## 可改文件
整个仓库（这是初始化任务）。

## 禁止项
- 不要删除 `docs/` 下任何已有文件。
- 不要提交到任何远程（本任务无远程）。
- 不要修改 `docs/tasks/01-p0-toolchain.md` 本身。
- 不要安装除 requirements.txt / package.json 之外的全局工具（不要改 PATH、不要 winget）。
