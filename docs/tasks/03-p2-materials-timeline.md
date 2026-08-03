# 任务 03：P2 素材上传 + 时间轴引擎

## 目标
实现素材上传（ffprobe 读元信息 + 缩略图）和 **`engine/timeline.py` 单一权威时长计算**（剪辑引擎的数学核心），暴露 `/timeline` 与素材/编辑 API，前端可上传素材并看到时间轴。

**关键原则**：`timeline.py` 是**唯一**计算时长/位置/offset 的地方。前端永远不自算时长，一律 GET `/timeline`。预览与导出（P3）都调用它。

## 技术上下文
- 参考 `docs/architecture.md`「剪辑引擎（engine/）」「REST API」。
- `config.py` 已有 `FFMPEG_PATH` / `FFPROBE_PATH`（imageio-ffmpeg 内置二进制，本地可用，无需网络）。
- `core/store.py` 已有项目 CRUD；素材文件存 `runtime/projects/<id>/shots/`，缩略图存 `work/`，元信息存 `project.json` 的 `materials` / `edits` 字段。
- 环境：python 用 `backend/.venv/Scripts/python.exe`。**禁止 pip/npm install**（沙箱无网）。本地 ffmpeg/ffprobe 可用。

## 数据模型（写入 `project.json`）

```jsonc
// project.json 追加字段
"materials": [                       // 按 shot_index 排序的已上传素材
  { "shot_index": 0, "filename": "shot_0.mp4", "duration": 6.0,
    "width": 720, "height": 1280, "fps": 30.0, "has_audio": true }
],
"edits": {
  "shots": [ { "trim_head": 0.0, "trim_tail": 0.0 }, ... ],   // 每个镜头，单位秒
  "junctions": [ { "transition": "fade", "fade_seconds": 0.5 }, ... ]  // 相邻镜头之间，N-1 个
}
```

## 步骤

### 1. 剪辑引擎 `backend/app/engine/`
- `engine/__init__.py`。
- `engine/media.py`：
  - `probe_video(path) -> dict`：用 `config.FFPROBE_PATH` 解析 JSON，返回 `{duration, width, height, fps, has_audio}`。ffprobe 命令示例：`ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -show_entries format=duration -of json <path>`；`has_audio` 用 `-select_streams a -show_entries stream=codec_type`。r_frame_rate 是 `"30/1"` 分数，转成 float。
  - `make_thumbnail(src, dst, at_seconds=1.0, width=320)`：用 `config.FFMPEG_PATH` 提取一帧：`ffmpeg -y -ss <at> -i <src> -frames:v 1 -vf scale=320:-1 <dst>`。
- `engine/timeline.py`：**纯函数，无 IO，可单测**。核心公式（必须严格遵守）：

  ```
  N = 镜头数（materials 长度）
  D[i] = 第 i 镜头源时长
  每镜头 trim：trim_head[i], trim_tail[i]（服务端钳制到 [0, D[i]-0.5] 且 trim_head+trim_tail ≤ D[i]-0.5）
  d_i = D[i] - trim_head[i] - trim_tail[i]        # 裁剪后可用时长
  每接缝 j=0..N-2：transition ∈ {hard, fade, crossfade}，fade_seconds F[j]
    F[j] 钳制到 [0, min(d_j, d_{j+1}, 1.0)]
  时长：
    hard / fade：start[i] = Σ_{k<i} d_k；total = Σ d_i        # fade 淡入淡出不改总时长
    crossfade ：offset[j] = Σ_{i≤j} d_i − Σ_{k≤j} F[k]；total = Σ d_i − Σ_j F[j]
  ```

  输出 `compute_timeline(materials, edits) -> dict`：
  ```jsonc
  {
    "segments": [ { "shot_index": i, "source_duration": D[i],
                    "trim_head": ..., "trim_tail": ...,
                    "used_duration": d_i, "start": start_i, "end": end_i } ],
    "junctions": [ { "index": j, "transition": "...", "fade_seconds": F[j],
                     "offset": <crossfade 时非 None> } ],
    "total_duration": <float>
  }
  ```
  边界：materials 空 → `segments: []`、`junctions: []`、`total_duration: 0`；N=1 → 无 junctions。

- `engine/__init__.py` 不引入 IO。

### 2. 素材 API `backend/app/api/materials.py`
- `POST /api/projects/{id}/materials`（multipart：字段 `shot_index` int + `file`）：
  - 校验 project 存在、shot_index ≥ 0 且不重复；落盘 `shots/shot_{index}.mp4`（用 `store.material_path`，含 shot_index 路径安全校验）。
  - `probe_video` 读元信息 → `make_thumbnail` 生成 `work/thumb_{index}.jpg` → 写入 `project.json` 的 `materials`（按 shot_index 排序，去重）→ 返回该素材 meta。
- `GET /api/projects/{id}/materials` → 素材列表（按 shot_index 排序）。
- `DELETE /api/projects/{id}/materials/{shot_index}` → 删文件 + 删 meta + 删缩略图，返回 204。
- `GET /api/projects/{id}/materials/{shot_index}/thumbnail` → 返回缩略图 jpg（404 若没有）。
- 上传用 `UploadFile`；类型校验：`.mp4/.mov/.mkv/.avi`（大写也接受），否则 400 中文 message。

### 3. 编辑与时间轴 API `backend/app/api/edits.py`
- `GET /api/projects/{id}/edits` → 当前 edits（无则返回默认：shots 全 `{trim_head:0,trim_tail:0}`、junctions 全 `{transition:"fade",fade_seconds:0.5}`，数量按 materials 推断）。
- `PUT /api/projects/{id}/edits` body 为 edits 结构 → 服务端**钳制**（每镜头 trim、每接缝 F[j]）→ 存盘 → 返回 `{edits: 生效后的值, timeline: compute_timeline(...)}`（回显钳制后的真实值）。
- `GET /api/projects/{id}/timeline` → `compute_timeline(当前 materials, 当前 edits)`。
- `main.py` 挂载 materials_router、edits_router（prefix `/api`）。

### 4. store 扩展 `backend/app/core/store.py`
- `material_path(project_id, shot_index) -> Path`：校验 shot_index 为非负 int，返回 `shots/shot_{i}.mp4`（目录自动建）。
- `thumbnail_path(project_id, shot_index) -> Path`：`work/thumb_{i}.jpg`。
- 读/写 `project.json` 的 `materials` / `edits` 字段（reuse `update_project` / `get_project`）。
- `list_materials(project_id)`、`get_edits(project_id)`、`save_edits(project_id, edits)` 等便捷函数。

### 5. 测试脚本 `backend/scripts/make_sample_shots.py`
用 ffmpeg（`config.FFMPEG_PATH`）生成 3 段竖屏测试素材，**每段头部 1 秒纯黑无效段**（验证"减头"用），有音轨：
```
ffmpeg -y -f lavfi -i "color=c=black:s=720x1280:d=1:r=30" -f lavfi -i "testsrc2=s=720x1280:d=5:r=30" -f lavfi -i "sine=frequency=440:duration=6" -filter_complex "[0:v][1:v]concat=n=2:v=1:a=0[v]" -map "[v]" -map 2:a -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest  shots/sample_0.mp4
```
（shot_1/2 用不同频率/颜色，头部同样 1s 黑。）输出到 `backend/scripts/samples/`。

### 6. 后端测试 `backend/app/tests/`
- `test_timeline.py`（**重点，覆盖全部公式**）：
  - 纯 hard：3 段 D=[6,6,6]，无 trim → total=18，segments start=[0,6,12]。
  - trim 生效：shot0 trim_tail=0.5、shot1 trim_head=1.0 → d=[5.5,5,6]，total=16.5。
  - trim 超限钳制：trim_head+trim_tail > D-0.5 → 钳到 D-0.5。
  - fade：F=0.5 不改 total（仍 = Σd）。
  - crossfade：3 段 D=[6,6,6] F=[0.5,1.0] → offset0=5.5、offset1=10.0，total=18-1.5=16.5。
  - crossfade F 钳制到 min(d_j,d_{j+1},1.0)。
  - 空 materials → total 0。
- `test_media.py`：用 `make_sample_shots` 生成的样本，`probe_video` 返回合理 duration/尺寸/has_audio；`make_thumbnail` 产出存在的 jpg。
- `test_materials_api.py`（TestClient）：上传 3 段（multipart）→ GET materials 排序正确、duration 合理 → 上传非法扩展名 400 → 重复 shot_index 409/400 → DELETE 后消失 → thumbnail 200。
- `test_edits_api.py`：PUT 超限 edits → 回显钳制值 + timeline 与手算一致；GET timeline 无素材时 total=0。

### 7. 前端（最小，重点是上传 + 时间轴展示）
- `api/types.ts` / `api/client.ts` 增加：uploadMaterial(FormData)、listMaterials、deleteMaterial、getThumbnailUrl、getEdits、putEdits、getTimeline。
- 新增视图 `materials`（素材管理）：
  - 上传：`<input type="file" multiple>`，选好后按文件顺序依次上传并自动分配 shot_index=当前数量开始；显示缩略图 + 文件名 + 时长 + 拍摄序号，可删除。
  - 时间轴条：GET /timeline 后渲染分段条（宽度=used_duration 占比），显示总时长。**不实现接缝调节 UI（那是 P3）**，只展示。
- App 状态路由增加 `materials`；从 script 视图可跳转素材页。

## 验收标准（Claude 独立复验）
1. `backend/.venv/Scripts/python.exe -m pytest backend/app/tests -q` 全部通过（尤其 test_timeline 覆盖 hard/fade/crossfade/钳制）。
2. `cd frontend && npm run build` 通过。
3. 真实 HTTP 冒烟：起 uvicorn → 建项目 → 用 sample 上传 3 段 → GET timeline 的 total 与 ffprobe 手算一致 → PUT 设 trim 后 timeline 更新 → 缩略图 200。
4. 无新增依赖。

## 可改文件
- `backend/app/engine/**`、`backend/app/api/materials.py`、`backend/app/api/edits.py`、`backend/app/main.py`、`backend/app/core/store.py`、`backend/scripts/**`、`backend/app/tests/**`
- `frontend/src/**`、`frontend/index.html`

## 禁止项
- 禁止 pip/npm install（沙箱无网）。
- 禁止修改/删除 `docs/` 文件。
- 禁止伪造测试结果：必须**真实运行** pytest 并贴真实输出。
- timeline 公式必须与本文档一致，不得自创算法；不确定就写"未验证"。
