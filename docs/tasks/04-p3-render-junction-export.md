# 任务 04：P3 拼接 + 缝合调节 + 导出

## 目标
实现视频渲染引擎（硬切/淡入淡出拼接）、接缝预览渲染、缝合调节 UI（「剪前段尾部 / 剪后段头部」按钮 + 转场选择）、全片导出 + 进度。**crossfade 本期不做（二期），UI 只提供 硬切 / 淡入淡出**。

## 技术上下文
- `engine/timeline.py` 是唯一权威时长来源，build/preview/export 全部调用它（不许另算时长）。
- `config.FFMPEG_PATH` 可用（imageio 内置）；无独立 ffprobe（`media.probe_video` 已用 ffmpeg 回退）。
- 素材：`runtime/projects/<id>/shots/shot_{i}.mp4`，元信息在 `project.json` 的 `materials`（含 duration/has_audio）与 `edits`。
- 输出规格：**1080x1920 竖屏 / fps=30 / yuv420p / h264 + aac**。

## 核心渲染 `engine/build.py`

### 输入与产出
`build_final(project_id, timeline) -> Path`：按 timeline（含 trim/转场）生成 filter_complex，调用 `config.FFMPEG_PATH` 输出到 `work/final.mp4`。供预览复用其内部纯函数（见下）。

### 每路输入的处理（N 个镜头）
第 i 镜头：源文件 `shots/shot_{i}.mp4`，源时长 `D_i`，裁剪 `trim_head_i / trim_tail_i`，可用时长 `d_i = D_i - TH - TL`（来自 timeline）。

**视频链** `[i:v]`：
```
trim=start=TH_i:end=D_i-TL_i,
setpts=PTS-STARTPTS,
scale=1080:1920:force_original_aspect_ratio=decrease,
pad=1080:1920:(ow-iw)/2:(oh-ih)/2,
fps=30,
format=yuv420p
```
**fade（仅需处）**：fade 不改变时长。若第 i 镜头左侧接缝 `j-1` 为 fade：链尾加 `,fade=t=in:st=0:d=F_{j-1}`；若右侧接缝 `j` 为 fade：加 `,fade=t=out:st=(d_i-F_j):d=F_j`。硬切不加 fade。

**音频链** `[i:a]`：
```
atrim=start=TH_i:end=D_i-TL_i,
asetpts=PTS-STARTPTS,
apad=whole_dur=d_i,           # 保证音频长度=视频长度，concat 稳定
aformat=sample_rates=44100:channel_layouts=stereo
```

**缺音轨的镜头**：`materials` 里 `has_audio=false` 的，**先预处理**——用 ffmpeg 给该镜头补一条静音音轨生成 `work/silent_{i}.mp4`（`-f lavfi -i anullsrc=r=44100:cl=stereo -shortest`），主拼接只用补齐后的文件。

### 拼接
```
[vi]...全部视频链、[ai]...全部音频链
[v0][v1]...[vN][a0][a1]...[aN]concat=n=N:v=1:a=1[vout][aout]
```
命令：
```
ffmpeg -y -i shot_0.mp4 -i shot_1.mp4 ... \
  -filter_complex "..." \
  -map "[vout]" -map "[aout]" \
  -c:v libx264 -preset medium -crf 20 -c:a aac -b:a 192k \
  -movflags +faststart work/final.mp4
```
**结构**：把「生成 filter_complex 字符串」写成纯函数 `build_filter_complex(timeline, material_paths) -> str`（可单测断言字符串），ffmpeg 执行封装成 `run_ffmpeg(args, on_progress=None)`。

## 接缝预览 `engine/junction.py`

`render_junction_preview(project_id, junction_index, before=1.5, after=1.5, width=360) -> Path`：渲染接缝 j 附近的低清预览（`work/preview_j{j}.mp4`，存在则直接返回）：
- 左段 = shot_j 的**末尾 before 秒**（在其裁剪后的 `d_j` 内取）：`trim=start=(d_j-before):end=d_j,setpts=PTS-STARTPTS,scale={w}:-2,fps=10,format=yuv420p`
- 右段 = shot_{j+1} 的**开头 after 秒**：`trim=start=0:end=after,setpts=PTS-STARTPTS,scale={w}:-2,fps=10,format=yuv420p`
- 接缝转场：hard 直接 concat；fade 在左段尾部加 `fade=t=out:st=(before-F):d=F`、右段头部加 `fade=t=in:st=0:d=F`（F=该接缝 fade_seconds）。
- 音频同主渲染（atrim+apad+aformat），concat n=2。编码：`-preset ultrafast -crf 28`。
- 注意：before/after 不能超过 d_j / d_{j+1}（服务端钳制到 min(before, d_j) 等）。

## 导出 job `engine/export.py` + `core/jobs.py`

- `core/jobs.py`：内存 job 注册表（dict）：`{job_id: {status: pending|running|done|failed, progress: 0-100, message, result}}`。线程安全（加锁）。`new_job() -> job_id`、`update_job(job_id, **)`、`get_job(job_id)`。
- `engine/export.py`：`start_export(project_id) -> job_id`：新 job → 后台线程跑：
  1. 读 materials/edits → `compute_timeline` → 校验至少 2 段素材（不足则 failed + 中文 message）。
  2. 预处理缺音轨素材。
  3. `build_filter_complex` → 用 `-progress pipe:1 -nostats` 起 ffmpeg 子进程，逐行解析 `out_time_us=`，`progress = out_time_us/1e6 / timeline.total_duration`，回调 `update_job(job_id, progress=...)`。
  4. 成功 → status=done，result={output: "exports/final.mp4", total_duration}，并 `copy` 到 `exports/final.mp4`。失败 → status=failed + stderr 摘要（中文）。
- **run_ffmpeg 失败处理**：非零退出 → 抛 `RenderError`，捕获后记 failed。

## API
- `PUT /api/projects/{id}/junctions/{j}` body `{trim_tail, trim_head, transition, fade_seconds}`：
  - `trim_tail` 写入 `edits.shots[j].trim_tail`；`trim_head` 写入 `edits.shots[j+1].trim_head`；transition/fade_seconds 写入 `edits.junctions[j]`。**全部走 timeline.normalize_edits 钳制**（一次重算整份 edits）。
  - 返回 `{edits: 生效值, timeline: compute_timeline(...)}`。
  - j 越界或相邻素材缺失 → 400/409 中文 message。
- `GET /api/projects/{id}/preview/junction/{j}?before=1.5&after=1.5&w=360` → 渲染并返回 mp4（FileResponse，media_type=video/mp4）。渲染失败 → 409 中文 message。
- `POST /api/projects/{id}/render/export` → `{job_id}`。
- `GET /api/jobs/{job_id}` → job 状态。
- `GET /api/projects/{id}/exports` → exports 目录文件列表（目前就 final.mp4）。
- `GET /api/projects/{id}/exports/final.mp4` → FileResponse 下载。
- `main.py` 挂载 `render`/`jobs` router；已有 `edits` router 增加 junction 子路由或新建 `api/render.py`。

## 前端
- `api/types.ts`/`client.ts`：`putJunction(projectId,j,body)`、`previewJunctionUrl(projectId,j,before,after)`、`startExport(projectId)`、`getJob(id)`、`exportUrl(projectId)`。
- 新视图 `edit`（时间轴编辑）：
  - TimelineBar：分段条（宽度=used_duration 占比），点接缝间隙选中 → 弹 JunctionPanel。
  - JunctionPanel：
    - 顶部 `PreviewPlayer`：`<video src={previewJunctionUrl(..., before=1.5, after=1.5)}>`，改动后 `?t=时间戳` 破缓存刷新。
    - 左块「剪前段尾部」按钮组：`−0.2 −0.1 +0.1 +0.2s` + 当前值（= edits.shots[j].trim_tail）。
    - 右块「剪后段头部」同构（= edits.shots[j+1].trim_head）。
    - 转场选择：硬切 / 淡入淡出（+ fade 时长步进 0.1s）。
    - 显示「预计总时长」（来自 PUT 返回的 timeline）。
  - 交互：每次按钮 → `putJunction` → 用返回的生效值更新本地 → debounce 400ms 刷新预览。
- 新视图 `export`：按钮「导出成片」→ POST export → 轮询 `/jobs/{id}`（1s）→ 进度条 → done 后显示「下载」链接（exports/final.mp4）。
- App 路由链：script → materials → edit → export。

## 测试
- `test_build.py`：
  - 纯函数断言：给定 timeline（2 段 fade）→ filter_complex 含 `trim=`、`scale=1080:1920`、`fade=t=out`、`fade=t=in`、`concat=n=2:v=1:a=1`；hard 无 fade。
  - 真渲染：用 sample 素材 build_final → `media.probe_video` 校验时长 ≈ timeline.total（±0.2s）、分辨率 1080x1920。
- `test_junction_api.py`：PUT junction（trim/transition）→ 回显钳制值 + timeline 更新；GET preview/junction 返回 mp4 且时长≈before+after。
- `test_export_api.py`：POST export → 轮询到 done → `exports/final.mp4` 存在 → probe 时长 ≈ timeline.total → GET exports/final.mp4 200。
- 渲染测试用 `backend/scripts/samples/` 小样本（每段约 6s），控制耗时。

## 验收标准（Claude 独立复验）
1. `pytest backend/app/tests -q` 全绿。
2. `cd frontend && npm run build` 通过。
3. 真实冒烟：起 uvicorn → 上传 3 样本 → 渲染接缝预览 mp4 → 导出成片 → probe 时长 = timeline 总时长（±0.2s）→ 下载 200。
4. 无新增依赖。

## 可改文件
- `backend/app/engine/**`、`backend/app/core/jobs.py`、`backend/app/api/edits.py`、`backend/app/api/render.py`(新)、`backend/app/main.py`、`backend/app/tests/**`
- `frontend/src/**`、`frontend/index.html`

## 禁止项
- 禁止 pip/npm install（沙箱无网）。
- 禁止修改/删除 `docs/` 文件。
- 禁止伪造测试结果：必须真实运行 pytest 并贴输出。
- 时长一律来自 timeline.py，不得自创算法。
- crossfade 本期不渲染（若 edits 里出现 crossfade，build 按 fade 处理并在 report 注明）。
