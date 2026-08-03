# 任务 02：P1 脚本生成（引导式问卷 + 模板）

## 目标
实现脚本生成模块：前端引导式问卷收集老板信息 → 后端模板生成器产出结构化脚本（`script.json`）→ 前端脚本卡片展示并可编辑。**不接入任何 LLM**，纯规则/模板，$0/单，可规模化。

## 技术上下文
- 参考 `docs/architecture.md`「脚本生成（scriptgen/）」与「REST API」。
- 已确认方案（2026-08-03）：**引导式问卷 + 模板生成**为主路径；Codex AI 生成器本期只做占位（registry 留空/not_configured），不实现 job 系统；AI 对话 agent 不做。
- 环境：Python 用 `backend/.venv/Scripts/python.exe`（已建好，依赖已装：fastapi/pydantic/pytest 等）。前端依赖已装好。
- **重要**：本任务 codex 运行在无外网的 sandbox 内。**禁止 pip install / npm install**。如确需新依赖，在报告里列出，由 Claude 在沙箱外安装。本地运行 pytest 用 venv python 即可（无需网络）。

## 目录现状
```
backend/app/
├─ main.py        # FastAPI，/api/health，CORS 已配
├─ config.py      # CODEX_BIN / PROJECTS_ROOT / CORS_ORIGINS / FFMPEG 探测
└─ tests/test_health.py
frontend/src/
├─ main.tsx  App.tsx
```

## 步骤

### 1. 后端数据模型 `backend/app/scriptgen/models.py`
Pydantic 模型：
```python
class BossInfo(BaseModel):
    restaurant_name: str = ""
    cuisine_type: str = "家常菜"
    signature_dishes: list[str] = []
    owner_persona: str = ""            # 老板人设/口播风格
    audience: str = ""                 # 目标人群
    video_style: str = "竖屏口播"
    target_duration_seconds: int = 60
    platform: str = "抖音"
    hook_preference: str = ""          # 开头偏好

class Shot(BaseModel):
    shot_index: int
    lines: str
    shooting_tips: str
    duration_hint_seconds: int
    location: str = ""
    angle: str = ""

class ScriptModel(BaseModel):
    title: str
    target_duration_seconds: int
    style: str
    opening_hook: str
    cta: str
    shots: list[Shot]
```

### 2. 生成器框架 `backend/app/scriptgen/__init__.py` + `generators/`
- `generators/__init__.py`：`ScriptGenerator` 协议（`generate(boss_info: BossInfo) -> ScriptModel`）+ 注册表 dict `REGISTRY`，`register(name)` / `get(name)`。
- `generators/template.py`：`TemplateGenerator`，**这是主路径**。按 `cuisine_type` 选择镜头模板（至少覆盖：川菜/火锅/烧烤/家常菜 + 通用 fallback）。模板结构（6 镜头通用骨架，时长分布适配 target_duration）：
  1. 开场 hook（优先用 hook_preference，否则按品类给默认抓人开场）
  2. 招牌菜特写展示（用 signature_dishes）
  3. 做法/亮点过程
  4. 老板出镜口播人设/故事（用 owner_persona / restaurant_name）
  5. 顾客反馈/现场氛围
  6. CTA 引导（用 restaurant_name）
  台词里用「{店名}」「{招牌菜}」「{人设}」等变量填充；`shooting_tips` 给具体的竖屏拍摄指导（机位/景别/动作）；每镜头 `duration_hint_seconds` 按 target_duration 合理分配（总和≈target）。确定性输出，不许引入随机。
- `generators/codex.py`：占位，`generate` 直接 `raise NotImplementedError("Codex AI 生成器二期接入")`，并注册为 "codex"。

### 3. 项目存储 `backend/app/core/`
- `core/__init__.py`。
- `core/store.py`：基于 `config.PROJECTS_ROOT`（`runtime/projects/<project_id>/`）的文件存储。project_id 用 `uuid4` 短码。函数：
  - `create_project(name: str) -> dict`（建目录 + 初始 `project.json`：`{id, name, boss_info: {}, script: null, created_at}`）
  - `list_projects() -> list[dict]`
  - `get_project(project_id) -> dict`（404 抛 `ProjectNotFoundError`）
  - `update_project(project_id, **patch) -> dict`（写回 project.json）
  - `save_script(project_id, script: ScriptModel) -> None`（写 `script.json`）
  - `load_script(project_id) -> ScriptModel | None`
  - **路径安全**：project_id 必须是 `[a-z0-9-]{8,}`，否则拒绝（防目录穿越）。

### 4. API 路由
- `api/__init__.py`。
- `api/projects.py`：
  - `POST /api/projects` body `{name}` → 项目 dict（201）
  - `GET /api/projects` → 项目列表
  - `GET /api/projects/{id}` → 项目 dict（含 boss_info、script 当前值）
  - `PATCH /api/projects/{id}` body 部分 `BossInfo` → 更新并返回
- `api/script.py`：
  - `POST /api/projects/{id}/script/template` body `BossInfo` → 调 `TemplateGenerator.generate` → `save_script` → 返回 `ScriptModel`
  - `GET /api/projects/{id}/script` → `ScriptModel`（无脚本时 404，message 中文）
  - `PUT /api/projects/{id}/script` body `ScriptModel` → 保存（手工编辑），返回保存后内容
- `main.py`：挂载两个 router（prefix `/api`）。项目不存在统一 404 中文 message。

### 5. 后端测试 `backend/app/tests/`
- `test_scriptgen.py`：
  - TemplateGenerator：给定完整 BossInfo → ScriptModel，断言 shots 数≥4、shot_index 连续、每镜头 lines/tips 非空、台词含「店名或招牌菜」之一、各镜头 duration 总和 ≈ target_duration（±10s）。
  - 多种 cuisine_type（含未收录的→走 fallback）都能出合法脚本。
  - `get("codex")` 的 generator 调用 raise NotImplementedError。
- `test_api.py`（TestClient）：
  - 建项目→PATCH boss_info→POST script/template→GET script 一致→PUT 手工改→GET 反映改。
  - 不存在的项目 GET → 404。
- 每个测试用独立临时 `PROJECTS_ROOT`（monkeypatch config），避免污染 runtime/。

### 6. 前端
- `src/api/types.ts`：与后端 Pydantic 对应的 TS 接口（BossInfo/Shot/ScriptModel/Project）。
- `src/api/client.ts`：fetch 封装 `createProject/getProjects/getProject/patchProject/generateTemplate/getScript/putScript`，基地址 `/api`。
- `src/App.tsx`：简单状态路由（不用 react-router，避免新依赖）：视图 = `list | setup | script`。
  - **list**：显示已有项目 + "新建项目"按钮。
  - **setup**：引导式问卷表单（店名/菜系/招牌菜/人设/目标人群/平台/目标时长）→「生成脚本」→ POST /script/template → 切到 script 视图。
  - **script**：脚本卡片（标题/hook/CTA + 每镜头卡片：序号/台词/拍摄要点/时长），**可编辑**（改完点「保存」→ PUT /script）。
- 样式简洁即可，能跑通功能优先。

## 验收标准（Claude 独立复验）
1. `backend/.venv/Scripts/python.exe -m pytest backend/app/tests -q` **全部通过**。
2. `cd frontend && npm run build` 通过。
3. 端到端（TestClient）：建项目→POST /script/template→返回合法 ScriptModel（shots≥4）。
4. 无新增后端依赖（若有，列出等 Claude 沙箱外安装）。
5. 未改动 `docs/` 任何文件、未提交远程。

## 可改文件
- `backend/app/` 全部（含新增模块/路由/测试）
- `frontend/src/**`、`frontend/index.html`、`frontend/package.json`（仅当确需新依赖，否则不改）
- 不得改 `backend/requirements.txt` 之外的依赖清单（若必须加依赖，报告里注明）

## 禁止项
- 禁止 pip install / npm install（沙箱无网络，会失败）。
- 禁止修改/删除 `docs/` 下文件（含本规格书）。
- 禁止伪造测试结果：验收必须**真实运行** `pytest` 并把输出贴进报告。
- 不确定就写"未验证"，不要假装完成。
