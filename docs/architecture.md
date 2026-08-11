# 架构（浓缩版）

完整计划见项目交接文档 `HANDOFF.md`。本文件是执行用的浓缩架构。

## 当前决策状态

### 未来主链（food-ip 作为老板使用的产品）

```text
Owner Input
→ Intent / Business Objective
→ confirmed_facts
→ missing_facts
→ 少量相关 Memory
→ 少量 relevant Knowledge
→ Creative Decision
→ Writer
→ Critic
→ Directed Rewrite
→ Shoot-ready Script
```

- 前台必须简单；**DO NOT build Multi-Agent**，优先 Workflow + structured modules。
- 单仓库逻辑边界：`knowledge_pipeline/` 是专业 Creative Knowledge 生产子系统；`backend/` / `frontend/` 当前保留产品运行时与 legacy 能力，未来如消费 Knowledge 只能通过已确认的稳定合约；`frontend/` 面向老板。Knowledge 最终服务于本仓库未来的 Creative Decision，生产实现不直接耦合进 backend。
- 当前主线：继续建设完整的 Food-IP Professional Creative Knowledge System。现有课程视频 pipeline 是已经实现并验证的一条知识摄入路径，不是知识来源全集。
- 知识源分层、准入、证据质量与时效治理尚未确认；本文件不预设具体层级、评分权重、Schema、检索方案或基础设施。
- Fact Contract / Fact Boundary、Memory、Retrieval、Creative Decision 属于未来产品主链能力，当前均 Deferred，待 Knowledge System 足够成熟且用户确认后再实现。

### 事实边界（Fact Boundary）— 未来产品长期原则（当前 Deferred）

区分：

```text
confirmed_facts   = 老板明确提供，或可信 Memory 中已确认的事实
creative_decision = AI 的创作判断与建议
missing_facts     = 创作需要但尚未确认的信息
```

Knowledge 只教 AI 怎么判断，不充当老板实际发生了什么；未确认信息标"需确认"或"如果事实成立，可以这样拍"。老板信息不足且某个事实确实是当前创作判断所必需时，只向老板做最少量关键追问；不重要的信息不追问，也不能编造。边界未来由 system / validation 层强制；当前不实施 Fact Contract 或兼容改造。

### Legacy 定位

旧脚本系统不删除，作为 **Legacy Script Generation = compatibility + baseline + reusable capabilities**。复用映射：

- `ResearchProfile` → 事实 / Memory 来源，不机械注入每条脚本
- `IPProfile` → 长期定位与表达约束
- `CreativeConversation` → 未来理解 Intent + 询问 missing facts
- `CreativeBrief` → 保留，是否扩展暂不决定
- `TopicCard` → optional interaction，不再必须生成
- Writer → 未来只根据 Creative Decision 写脚本
- Director Review → 优先复用为 Critic
- `revise_script_candidate` → 优先复用为 Directed Rewrite
- materials / timeline / FFmpeg / export → 全部保留

固定 strategy / 内容桶继续服务 legacy，不再决定未来"今天拍什么"。

## Knowledge System 当前主线

当前继续建设完整的 Professional Creative Knowledge System。现有视频生产链是已实现并验证的一条知识摄入路径：

```text
source video → transcribe → refine → per-source persistence → global snapshot
```

未来知识可以来自更多高质量来源，但知识源分层、准入标准、证据质量与时效治理仍待讨论确认；不要把任何未确认的来源清单或治理方案写成既定架构。

## 产品流水线（当前真实流程 = Legacy / baseline）

> 以下为当前已实现的流水线，定位为兼容与基线；未来主链见上方"当前决策状态"。

1. 五步深度调研 → 产出结构化 `ResearchProfile`
2. IP 定位确认（`IPProfile`）→ AI 共创确认 `CreativeBrief`（均可跳过）
3. `TopicCard` 推荐选题（可跳过；一旦选定，三套候选锁同一主题）
4. `ScriptBundle` 生成（规则模板 / AI 生成）→ 程序硬规则校验
5. AI Director Review：独立只读 9 维评分 → 程序化低分判定 → AI 局部修稿（只修指定低质量位置）→ 再次程序硬规则校验（自动触发修稿尚未接入）
6. 老板比较并选择候选，可手工修改 → 产出当前 `script.json`
7. 老板按脚本拍摄 N 个镜头素材
8. 素材自动拼接、接缝调节并导出成片
9. AI 脚本增强与视频润色（二期）：接口占位，不接真实模型

## 目录结构

```
food-ip/
├─ backend/
│  ├─ .venv/                  # Python 3.10+ 虚拟环境
│  ├─ requirements.txt
│  ├─ app/
│  │  ├─ main.py              # FastAPI 入口，/api/health，CORS，静态托管
│  │  ├─ config.py            # 读 .env：CODEX_BIN、PROJECTS_ROOT、CORS_ORIGINS；ffmpeg/ffprobe 定位
│  │  ├─ api/                 # projects.py script.py creative.py materials.py edits.py render.py polish.py jobs.py
│  │  ├─ core/                # store.py(项目=文件夹状态读写) project.py(ProjectState) jobs.py(内存job)
│  │  ├─ scriptgen/           # models.py bundles.py ai.py creative.py quality.py review.py generators/(template.py codex.py)
│  │  ├─ engine/              # ffmpeg.py media.py timeline.py build.py junction.py export.py
│  │  ├─ polish/              # contract.py registry.py providers/null.py
│  │  └─ tests/
│  └─ scripts/                # make_sample_shots.py e2e_smoke.py sync_official_guidance.py
├─ frontend/                  # Vite + React + TS；dev proxy /api → http://127.0.0.1:8000
├─ knowledge_pipeline/        # Professional Creative Knowledge System
├─ docs/
│  ├─ architecture.md
│  ├─ api.md
│  ├─ polish-interface.md
│  ├─ deploy.md
│  ├─ questionnaire-design.md
└─ runtime/projects/<id>/     # gitignore；project.json script_bundle.json script.json script_versions.json ip_profile.json creative_conversations/ shots/ work/ exports/
```

## 核心设计要点

### 脚本生成（scriptgen/）
- 已确认方案（2026-08-04）：**深度调研 + 规则评分 + DeepSeek 结构化生成 + 本地质检**为脚本主路径；模型名和接口地址通过环境变量替换。
- `ResearchProfile` 分为门店、老板、顾客目标、拍摄条件；保存时投影出旧 `BossInfo`，兼容原单模板接口和旧项目。
- `bundles.py` 对招牌菜、老板故事、后厨揭秘、顾客问题、经营纪实五种策略评分，避开不可出镜或不可拍后厨等条件，选择 2–5 个互不重复候选。
- `ai.py` 调用 OpenAI 兼容的 Chat Completions JSON 输出，默认模型为 `deepseek-v4-flash`；输出经 Pydantic 与业务规则校验，失败时携带错误原因重试一次。
- `ScriptBundle` 保存候选的适配分、理由、难度、场景与完整六镜头脚本；只有用户选择后才写入当前 `script.json`。
- `script.json` 仍是当前脚本的唯一权威文件；每次模板生成、候选选择或手工保存实际改变当前脚本时，同时向 `script_versions.json` 追加完整快照。旧项目首次查询历史时会把现有脚本登记为兼容基线。
- `template.py` 保留为旧版单脚本兼容入口。
- `codex.py`（可选增强）：Codex AI 生成器占位，当前 raise NotImplementedError。
- 输出 schema（`script.json`）：
  ```json
  { "title": "", "target_duration_seconds": 60, "style": "",
    "opening_hook": "", "cta": "", "quality_risks": [],
    "shots": [ { "shot_index": 1, "lines": "", "shooting_tips": "",
                 "duration_hint_seconds": 8, "location": "", "angle": "",
                 "tone": "", "emotion": "", "speech_rate": "",
                 "pause_guidance": "", "expression_guidance": "" } ] }
  ```
- AI 生成后增加轻量风险标记，分类为真实性、可拍摄性和 IP 一致性；只提示，不自动修改脚本，不作为法律审核。
- TopicCard 锁题：用户选择 TopicCard 后，三套候选必须围绕同一主题；锁题模式下 `strategy` 只是表现角度（Hook、叙事方式、证据展示、老板表达、镜头组织），底层 strategy 不再拆成不同主题；未选 TopicCard 时保留原 strategy 各开一题逻辑。
- AI 编导审稿（`review.py`）：通过程序硬校验后自动对候选做独立 9 维评分（opening_hook_strength、oral_naturalness、information_density、progression、evidence_strength、ip_alignment、shootability、ad_feeling、distinctiveness，各 1-10）；编导只读，不重写脚本、不重新选题、不修改事实 / IP / Brief / TopicCard。`ScriptBundle` 新增可选 `review` / `review_error`，旧数据无此字段仍兼容读取。
- 程序化低分判定（`review.judge_revision_needed`）：纯程序规则，总分 < 7.0 或任一关键维度（opening_hook_strength / oral_naturalness / progression / evidence_strength / shootability）< 6 判定需修稿；AI 的 `should_revise` 仅作参考。
- AI 局部修稿（`review.revise_script_candidate`）：只修改 verdict / issues 指向的镜头或字段（锚点限制），补丁式输出（`extra=forbid`）结构上杜绝整篇重写；`candidate_id` / `strategy` 不可变；锁题时禁止改标题（防换题）；修稿后复用 `ai._validate_candidates` 现有硬规则校验并重新生成 `quality_risks`。主生成 Schema `AIBundleOutput.candidates` 保持 `min_length=2` 不放宽。**自动根据低分触发修稿尚未接入生成流程**。
- 审稿失败隔离：AI 编导审稿失败不丢弃已生成候选，仅记录 `review_error` 与 warning；`review_error` 只代表审稿失败，不代表脚本生成失败。

### 剪辑引擎（engine/）
- **`timeline.py` 唯一权威时长来源**：计算每镜头裁剪后时长 d_i、接缝 offset、总时长。前端一律 GET `/timeline`。
- trim 模型：每镜头 `trim_head_i`/`trim_tail_i`；每接缝 `junction_j` = `{trim_tail_j, trim_head_{j+1}, transition(hard|fade|crossfade), fade_seconds}`。
- 转场时长公式：
  - 硬切：`total = Σ d_i`
  - 淡入淡出（默认）：接缝 fade-out+fade-in 到黑，`total = Σ d_i`（时长不变）
  - crossfade：`offset_j = Σ_{i≤j} d_i − Σ_{k≤j} F_k`；`total = Σ d_i − Σ F_j`
  - 钳制：`trim_head+trim_tail ≤ D_i−0.5`；`F_j ≤ min(d_j, d_{j+1})` 且 `F_j ≤ 1.0`
- `build.py`：timeline → ffmpeg filter_complex。统一归一化 1080:1920 / fps=30 / yuv420p；hard/fade 使用 concat，fade 在片段首尾处理，crossfade 使用 `xfade + acrossfade` 按权威 offset 真正交叠。最终导出可附加镜头级 ASS 字幕和单项目 BGM 混音，但不改变 timeline 时长计算。
- 导出前按 `script.shots[].shot_index` 校验素材完整性；导出完成后重新探测输出文件，验证文件存在、音视频流、1080×1920 及总时长误差。低分辨率输入只产生 warning，不做超分。
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
| GET/PUT | /api/projects/{id}/research | 读取/保存深度调研档案 |
| POST | /api/projects/{id}/script-bundles/template | 规则评分生成多套脚本 |
| POST | /api/projects/{id}/script-bundles/ai | 规则选题并由 AI 生成、校验多套脚本 |
| GET | /api/projects/{id}/script-bundles/latest | 查看最近一次脚本方案 |
| POST | /api/projects/{id}/script-bundles/{bundle}/select/{script} | 选择当前拍摄脚本 |
| GET/PUT | /api/projects/{id}/ip-profile | 读取/编辑 IP 定位（confirm 后只读确认态） |
| POST/GET | /api/projects/{id}/creative-conversations | AI 共创会话（追信、确认 Brief、生成 TopicCard / 候选） |
| POST | /api/projects/{id}/script/template | 旧版单模板兼容入口 |
| GET/PUT | /api/projects/{id}/script | 查看/手工编辑脚本 |
| POST | /api/projects/{id}/materials | 上传素材(multipart+shot_index, ffprobe) |
| PUT | /api/projects/{id}/materials/{shot_index} | 替换指定镜头的单个素材 |
| DELETE/GET | /api/projects/{id}/materials/{shot_index} | 删/流式播放(Range) |
| GET/POST/DELETE | /api/projects/{id}/bgm | 查询、上传或删除单项目 BGM |
| GET | /api/projects/{id}/timeline | ★权威时长 |
| PUT | /api/projects/{id}/junctions/{j} | 设置接缝(钳制并回显) |
| GET | /api/projects/{id}/preview/junction/{j} | 接缝预览 mp4 |
| POST | /api/projects/{id}/render/export | 全片导出 → job |
| GET | /api/jobs/{id} | 统一轮询 |
| POST | /api/projects/{id}/polish/junctions/{j} | 一期恒 not_configured |

错误约定：400 参数错 / 404 不存在 / 409 依赖未就绪 / 422 Pydantic 校验失败（含中文 message）。
