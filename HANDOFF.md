# food-ip 项目交接文档（HANDOFF）

> 生成日期：2026-08-03。本文件是任何 agent / 新会话接手本项目的**第一入口**。
> 先读本文件 → 再读 `CLAUDE.md` → 需要细节时查 `docs/architecture.md`。
>
> ⚠️ **历史交接文档**：本文反映 2026-08-03 的早期 MVP 状态，部分描述已过时。当前真实状态以 `AGENTS.md`、`docs/architecture.md`、`docs/api.md` 及代码 / 测试为准。脚本生成已升级为「规则选题 + DeepSeek 生成 + 程序质检」，并新增 IP 定位 / AI 共创 / TopicCard 锁题 / AI 编导审稿（自动局部修稿未上线）；文中「纯规则模板」「AI 对话共创未实现」等均为旧状态。

---

## 1. 项目是什么

给餐饮店老板打造「个人 IP 口播短视频」的工具。老板（非技术人员）在 Web 界面完成：
**引导问卷 → 模板生成脚本 → 上传素材 → 时间轴调缝 → 导出成片**。

核心流水线：
```
① 脚本生成（独立可插拔模块：引导问卷 + 模板，$0/单，可规模化）
② 老板按脚本拍摄 N 个镜头
③ 素材自动拼接（硬切 / 淡入淡出，1080×1920 竖屏）
④ 缝合处调节：每个接缝可减 0.x~2s 无效片段（剪前段尾部 + 剪后段头部）+ 转场选择
⑤ AI 润色（二期占位，恒 not_configured）
```

## 2. 当前状态：✅ MVP 完整交付（P0–P4 全部验收通过）

| 阶段 | 内容 | 状态 | 测试证据 |
|---|---|---|---|
| P0 | 骨架 + 工具链 | ✅ | health 200，ffmpeg 探测成功 |
| P1 | 脚本生成（问卷+模板，6镜头） | ✅ | pytest + 真实 HTTP 冒烟 |
| P2 | 素材上传 + timeline 权威时长 | ✅ | 24 tests |
| P3 | 拼接 + 缝合调节 + 导出 | ✅ | 29 tests，**成片时长 = timeline 零误差** |
| P4 + 后续维护 | 润色占位、静态托管、编号修复、真实 crossfade、前端拆分 | ✅ | **37 tests 全绿** |

**端到端可用**：`uvicorn` 单进程托管前端 + 后端；`POST /polish/junctions/{j}` 返回 `not_configured`。

## 3. 技术栈

- **后端**：Python 3.10+（当前 venv 为 3.12.13，位于 `backend/.venv`）+ FastAPI + uvicorn + Pydantic；`imageio-ffmpeg` 内置静态 ffmpeg（缺少 ffprobe 时自动回退探测）。
- **前端**：Vite + React 19 + TypeScript（`frontend/`），dev proxy `/api → 127.0.0.1:8000`。
- **脚本生成**：规则选题 + DeepSeek 生成 + 程序质检为主路径（旧模板保留为兼容入口）。Codex AI 生成器为占位（raise NotImplementedError）。
- **剪辑引擎**：`engine/`，ffmpeg 后端渲染。

## 4. 关键目录结构

```
food-ip/
├─ HANDOFF.md / CLAUDE.md / README.md
├─ backend/
│  ├─ .venv/                  # Python 3.12.13（已装依赖）
│  ├─ app/
│  │  ├─ main.py config.py
│  │  ├─ api/     projects.py script.py materials.py edits.py render.py polish.py
│  │  ├─ core/    store.py jobs.py
│  │  ├─ scriptgen/  models.py generators/(template.py codex.py)
│  │  ├─ engine/   timeline.py media.py build.py junction.py export.py
│  │  ├─ polish/   contract.py registry.py providers/null.py
│  │  └─ tests/   （37 个测试）
│  └─ scripts/  make_sample_shots.py e2e_smoke.py
├─ frontend/  src/App.tsx（状态与流程编排）+ src/views/（项目、问卷、脚本、素材、接缝、导出视图）
├─ docs/
│  ├─ architecture.md  api.md  deploy.md  polish-interface.md  questionnaire-design.md
└─ runtime/projects/<id>/   # 项目数据（gitignore）：project.json script.json shots/ work/ exports/ log/
```

## 5. 核心设计原则（改代码前必读）

1. **`engine/timeline.py` 是唯一权威时长来源**。前端/预览/导出永远用 `compute_timeline()` 的结果，禁止自行算时长。
   - 转场：hard/fade 不改总时长（`total=Σd`）；crossfade `offset_j=Σ_{i≤j}d_i−Σ_{k≤j}F_k`、`total=Σd−ΣF`。
   - 钳制：`trim_head+trim_tail ≤ D−0.5`；`F ≤ min(d_j,d_{j+1},1.0)`。
2. **脚本生成免费可规模化**：模板为主（`scriptgen/generators/template.py`），任何新增生成器走 `ScriptGenerator` 协议 + 注册表。
3. **项目 = 一个文件夹**：`runtime/projects/<id>/`，读写全经 `core/store.py`（含 path 安全校验）。
4. **AI 润色二期接入方式**：输入=已渲染片段文件、输出=替换片段文件，加一个 provider 即可，不改引擎（见 `docs/polish-interface.md`）。

## 6. 运行与测试

```bash
# 后端测试（当前 104 个）
cd C:\Users\HP\food-ip
backend\.venv\Scripts\python.exe -m pytest backend\app\tests -q

# 前端构建
cd frontend && npm run build

# 启动（单进程，前端已 build 后托管 dist）
backend\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# 真实冒烟（自动起服务器 + 上传样本 + 导出校验）
backend\.venv\Scripts\python.exe backend\scripts\e2e_smoke.py
```

环境变量（`.env`）：`CODEX_BIN`、`PROJECTS_ROOT`（默认 `runtime/projects`）、`CORS_ORIGINS`、`FRONTEND_DIST`。

## 7. 已知限制 / 环境偏差（重要）

1. **无独立 ffprobe**：`config.FFPROBE_PATH=None`，`engine/media.py` 用 ffmpeg 回退解析（实测时长准确）。建议后续装正式 ffmpeg/ffprobe。
2. **Python 3.12.13 而非 3.10**：机器原装 3.10 是裁剪版（缺 venv），兼容性已验证。
4. **本机系统代理**：Python httpx 默认 `trust_env=True` 会走系统代理导致 localhost 502，测试脚本必须 `trust_env=False`（`e2e_smoke.py` 已修）。**curl 在 Git Bash 传中文 JSON body 会编码损坏**，调试用 python httpx。
5. **crossfade 已渲染**：主渲染和接缝预览均使用 `xfade + acrossfade`，时长与 `timeline.total_duration` 一致。
6. **前端仍使用轻量状态路由**：视图已从 `App.tsx` 拆到 `src/views/`；后续页面继续增长时可引入正式 router。

## 8. v2 路线图（未实现，按优先级）

1. **引导问卷升级**：`docs/questionnaire-design.md` 的 4 步引导（需给 `BossInfo` 加 `usp/story/cta_goal` 3 字段）。
2. **AI 对话共创 agent**：已实现（IP 定位 + CreativeConversation 共创，见 `AGENTS.md` / `docs/architecture.md`）；此处为旧路线图遗留项。流式体验与打磨仍为后续方向。
3. 正式 ffmpeg/ffprobe 安装（winget 或静态包）。
4. 手机端 / 云端部署（`docs/deploy.md` 有思路：后端搬云 + 前端适配）。

## 9. Git 历史

- `301516c` P0 基线 · `7f9b031` P1 脚本生成 · `b0d6d56` P2 素材+时间轴 · `4f78bf4` P3 拼接+导出 · （P4 + 本文件待提交）

## 10. 下次接手建议

1. 先跑 `pytest` 确认基线绿。
2. 若调产品 → 优先做问卷升级（用户已确认 MVP 后再优化问卷）。
