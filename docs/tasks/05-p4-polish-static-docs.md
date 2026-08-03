# 任务 05：P4 润色占位 + 静态托管 + 文档收尾

## 目标
完成 MVP 收尾：AI 润色接口占位（恒 not_configured）、FastAPI 托管前端构建产物（单进程跑整个应用）、补齐部署/API 契约/润色接口文档、README、轻量体验打磨。**这是最后一个委托任务。**

## 技术上下文
- 后端 FastAPI 已挂 `/api` 各 router；前端 `frontend/`（Vite+React+TS）。
- `frontend/dist/` 被 gitignore（构建产物不入库）。
- 参考 `docs/architecture.md` 的「AI 润色占位（polish/）」与「REST API」。

## 步骤

### 1. AI 润色占位 `backend/app/polish/`
- `polish/__init__.py`。
- `polish/contract.py`（Pydantic + 类型）：
  ```python
  class SegmentRef(BaseModel):
      project_id: str
      junction_id: int | None = None
      src_file: str = ""
      range_seconds: tuple[float, float] | None = None

  class PolishRequest(BaseModel):
      segment: SegmentRef
      goal: Literal["harmonize_junction","stabilize","relight","fix_audio"] = "harmonize_junction"
      params: dict[str, Any] = {}

  class PolishResult(BaseModel):
      segment_id: str
      output_file: str | None = None
      status: Literal["pending","running","done","failed","not_configured"]
      message: str | None = None
  ```
- `polish/registry.py`：provider 注册表 `REGISTRY: dict[str, PolishProvider]` + `register/get`。`PolishProvider` 协议：`async polish(req) -> PolishResult`。
- `polish/providers/null.py`：`@register("null")`，`polish` 直接返回 `PolishResult(status="not_configured", message="AI 润色功能尚未接入（二期实现）")`。
- API `api/polish.py`：
  - `POST /api/projects/{id}/polish/junctions/{j}` body `{goal, params}` → 先校验项目与接缝存在（不存在 → 404/400 中文 message）→ 调 null provider → 返回 `PolishResult(status="not_configured")`（HTTP 200）。
  - `GET /api/projects/{id}/polish/providers` → 返回已注册 provider 列表（`["null"]`），供前端判断能力。
- `main.py` 挂载 polish router。

### 2. 静态托管 `backend/app/main.py`
- `config.py` 增加 `FRONTEND_DIST: Path`（默认 `<仓库根>/frontend/dist`）。
- `main.py` 在挂完所有 API router **之后**：
  - 若 `FRONTEND_DIST/index.html` 存在：挂 `StaticFiles(directory=FRONTEND_DIST, html=True)` 到 `/`（先 `/assets` 静态、再 `/` SPA 兜底，用 `app.mount("/", ...)` 放最后，API 路由优先）。
  - 若不存在：`GET /` 返回 JSON `{"message": "前端未构建，请先运行 npm run build", "docs": "/docs"}`。
- 验证：`npm run build` 后，`GET /` 返回前端 HTML，`GET /api/health` 仍正常。

### 3. 文档
- `README.md`（仓库根）：
  - 项目一句话介绍、功能流水线（脚本→拍摄→拼接→缝合→导出→润色二期）。
  - 快速开始：后端（`python -m venv backend/.venv` + `pip install -r backend/requirements.txt` + `uvicorn backend.app.main:app --port 8000`）、前端 dev（`npm install` + `npm run dev`）、单进程生产（`npm run build` 后后端直接托管）。
  - 环境变量说明（CODEX_BIN / PROJECTS_ROOT / CORS_ORIGINS）。
  - 测试：`backend/.venv/Scripts/python.exe -m pytest backend/app/tests -q`。
- `docs/api.md`：**完整 REST 契约**——逐条列出当前实现的所有端点（method/路径/请求体/响应/错误码 400/404/409/422），与实现一致。以实际代码为准。
- `docs/deploy.md`：本地运行、单进程部署、未来云端部署（后端搬云服务器 + 前端适配手机）的说明。
- `docs/polish-interface.md`：二期润色接口契约（PolishRequest/PolishResult、provider 接入方式：输入已渲染片段文件、输出替换文件路径，改 provider 不改引擎）。

### 4. 轻量体验打磨（前端）
- 各视图空状态文案（无项目/无素材/无脚本时给中文引导）。
- 操作反馈：导出失败显示 job 的 message；上传中按钮禁用；加载加简单文案。
- 不改核心逻辑，只补状态与文案。

## 测试
- `test_polish_api.py`：POST /polish/junctions/0 → 200 + status=not_configured + message 中文；不存在的项目 → 404；不存在的接缝 → 400/404；GET /polish/providers → `["null"]`。
- `test_static.py`：GET `/` → 200（有 dist 返回 HTML 或 message JSON 均可）；GET /api/health 不受影响。
- 现有 29 个测试保持全绿。

## 验收标准（Claude 独立复验）
1. `pytest backend/app/tests -q` 全绿（≥30）。
2. `cd frontend && npm run build` 通过，然后起 uvicorn：`GET /` 返回前端 HTML；`GET /api/health` 200。
3. `POST /polish/junctions/{j}` 返回 `not_configured`。
4. 文档（api.md/deploy.md/polish-interface.md/README.md）与实现一致。
5. 无新增依赖。

## 可改文件
- `backend/app/polish/**`（新）、`backend/app/api/polish.py`（新）、`backend/app/main.py`、`backend/app/config.py`、`backend/app/tests/**`
- `README.md`、`docs/api.md`、`docs/deploy.md`、`docs/polish-interface.md`
- `frontend/src/**`

## 禁止项
- 禁止 pip/npm install（沙箱无网）。
- 禁止修改/删除 `docs/` 下已有文件（04-report.md 等），只能**新增** api.md/deploy.md/polish-interface.md。
- 禁止伪造测试结果：必须真实运行 pytest 并贴输出。
- 不实现真实润色模型；`not_configured` 是唯一合法结果。
