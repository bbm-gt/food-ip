# 架构（浓缩版，供 Codex 执行时参考）

完整计划见 `C:\Users\HP\.claude\plans\merry-stargazing-hopper.md`。本文件是执行用的浓缩架构。

## 产品流水线

1. 脚本生成（独立可插拔模块）：模板生成（免费主路径）| Codex AI（可选增强）| 手工编辑 → 产出 `script.json`
2. 老板按脚本拍摄 N 个镜头素材
3. 素材自动拼接成一条视频（顺序 + 简单转场）
4. 缝合处调节：每个接缝可减 0.x~2 秒无效片段（剪前段尾部 + 剪后段头部）+ 转场选择
5. AI 润色（二期）：接口占位，不接真实模型

## 目录结构

```
food-ip/
├─ backend/
│  ├─ .venv/                  # Python 3.10+ 虚拟环境
│  ├─ requirements.txt
│  ├─ app/
│  │  ├─ main.py              # FastAPI 入口，/api/health，CORS，静态托管
│  │  ├─ config.py            # 读 .env：CODEX_BIN、PROJECTS_ROOT、CORS_ORIGINS；ffmpeg/ffprobe 定位
│  │  ├─ api/                 # projects.py script.py materials.py edits.py render.py polish.py jobs.py
│  │  ├─ core/                # store.py(项目=文件夹状态读写) project.py(ProjectState) jobs.py(内存job)
│  │  ├─ scriptgen/           # generators/(template.py codex.py) codex_client.py prompts.py models.py validate.py
│  │  ├─ engine/              # ffmpeg.py media.py timeline.py build.py junction.py export.py
│  │  ├─ polish/              # contract.py registry.py providers/null.py
│  │  └─ tests/
│  └─ scripts/                # make_sample_shots.py e2e_smoke.py
├─ frontend/                  # Vite + React + TS；dev proxy /api → http://127.0.0.1:8000
├─ docs/
│  ├─ architecture.md
│  ├─ api.md
│  ├─ polish-interface.md
│  ├─ deploy.md
│  ├─ claude-codex-workflow.md
│  └─ tasks/NN-*.md
└─ runtime/projects/<id>/     # gitignore；project.json script.json shots/ work/ exports/ log/
```

## 核心设计要点

### 脚本生成（scriptgen/）
- 已确认方案（2026-08-03）：**引导式问卷 + 模板生成**为主路径，免费可规模化；Codex AI 生成仅作可选增强；AI 对话共创 agent **暂不做**（可二期）。
- `ScriptGenerator` 协议 + 注册表，一个生成器一个文件。
- `template.py`（默认主路径）：**引导式问卷**收集老板信息（菜系/招牌菜/人设/风格/时长）→ 按菜系/内容类型的镜头模板库填充 → 即时返回脚本骨架，$0/单。
- `codex.py`（可选增强）：`codex exec --ephemeral -s read-only -o <out> -C <项目目录> "<prompt>"`，240s 超时+kill。（注意：`codex exec` 不接受 `--ask-for-approval`。）
- 输出 schema（`script.json`）：
  ```json
  { "title": "", "target_duration_seconds": 60, "style": "",
    "opening_hook": "", "cta": "",
    "shots": [ { "shot_index": 1, "lines": "", "shooting_tips": "",
                 "duration_hint_seconds": 8, "location": "", "angle": "" } ] }
  ```
- `validate.py`：剥 ```json 围栏 → 括号匹配提取 → Pydantic 校验，失败带错重试 1 次。

### 剪辑引擎（engine/）
- **`timeline.py` 唯一权威时长来源**：计算每镜头裁剪后时长 d_i、接缝 offset、总时长。前端一律 GET `/timeline`。
- trim 模型：每镜头 `trim_head_i`/`trim_tail_i`；每接缝 `junction_j` = `{trim_tail_j, trim_head_{j+1}, transition(hard|fade|crossfade), fade_seconds}`。
- 转场时长公式：
  - 硬切：`total = Σ d_i`
  - 淡入淡出（默认）：接缝 fade-out+fade-in 到黑，`total = Σ d_i`（时长不变）
  - crossfade：`offset_j = Σ_{i≤j} d_i − Σ_{k≤j} F_k`；`total = Σ d_i − Σ F_j`
  - 钳制：`trim_head+trim_tail ≤ D_i−0.5`；`F_j ≤ min(d_j, d_{j+1})` 且 `F_j ≤ 1.0`
- `build.py`：timeline → ffmpeg filter_complex。统一归一化 1080:1920 / fps=30 / yuv420p；hard/fade 使用 concat，fade 在片段首尾处理，crossfade 使用 `xfade + acrossfade` 按权威 offset 真正交叠。
- `junction.py`：渲染某接缝前后 1.5s 低分辨率预览 mp4。

### 缝合调节 UI（前端）
- TimelineBar 分段条（宽度=d_i 占比），点接缝间隙 → JunctionPanel。
- JunctionPanel：接缝预览播放器 + 「剪前段尾部」「剪后段头部」各一组 −0.2/−0.1/+0.1/+0.2s 按钮 + 转场选择 + 预计总时长。
- 每次按钮 → `PUT /junctions/{j}`（服务端钳制并回显）→ debounce 400ms 重渲染预览。改即持久化。

### AI 润色占位（polish/）
- `PolishRequest{segment(project_id, junction_id, src_file, range_seconds), goal(harmonize_junction|stabilize|relight|fix_audio), params}`
- `PolishResult{segment_id, output_file, status(pending|running|done|failed|not_configured), message}`
- provider 注册表 + null provider 恒返回 not_configured。

## REST API（概要）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/health | 自检，含 ffmpeg/ffprobe 路径 |
| POST/GET | /api/projects | 建/列项目 |
| GET/PATCH | /api/projects/{id} | 项目状态 / 保存老板信息 |
| POST | /api/projects/{id}/script/template | 模板即时生成脚本（免费主路径） |
| POST | /api/projects/{id}/script/generate | AI 增强生成 → {job_id} |
| GET/PUT | /api/projects/{id}/script | 查看/手工编辑脚本 |
| POST | /api/projects/{id}/materials | 上传素材(multipart+shot_index, ffprobe) |
| DELETE/GET | /api/projects/{id}/materials/{shot_index} | 删/流式播放(Range) |
| GET | /api/projects/{id}/timeline | ★权威时长 |
| PUT | /api/projects/{id}/junctions/{j} | 设置接缝(钳制并回显) |
| GET | /api/projects/{id}/preview/junction/{j} | 接缝预览 mp4 |
| POST | /api/projects/{id}/render/export | 全片导出 → job |
| GET | /api/jobs/{id} | 统一轮询 |
| POST | /api/projects/{id}/polish/junctions/{j} | 一期恒 not_configured |

错误约定：400 参数错 / 404 不存在 / 409 依赖未就绪 / 422 Pydantic 校验失败（含中文 message）。
