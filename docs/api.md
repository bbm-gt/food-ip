# REST API 契约

本文记录当前代码已经实现的 HTTP 接口。API 基础前缀为 `/api`，请求与 JSON 响应均使用 UTF-8。

## 通用约定

- 成功响应为 JSON，缩略图、预览视频和最终成片除外；删除素材成功返回空的 `204`。
- 业务错误通常为 `{"message": "中文说明"}`。
- `400` 表示 ID、序号或素材参数错误；`404` 表示项目、脚本、素材、文件或任务不存在；`409` 表示素材重复或渲染前置条件不满足；`422` 表示请求体、表单或查询参数没有通过 FastAPI/Pydantic 校验。
- 项目 ID 必须匹配 `[a-z0-9-]{8,}`；不合法为 `400`，格式合法但不存在为 `404`。
- 下文 `Project` 包含 `id`、`name`、兼容字段 `boss_info`、深度档案 `research`、当前选中 `script`、最近一次 `script_bundle`、`materials`、`edits`、`created_at`；`Script` 包含 `title`、`target_duration_seconds`、`style`、`opening_hook`、`cta` 和 `shots`。

## 系统

### `GET /api/health`

- 请求：无。
- `200`：`{"ok": true, "ffmpeg": string|null, "ffprobe": string|null, "codex_bin": string, "projects_root": string}`。
- 错误：无业务错误。

## 项目

### `POST /api/projects`

- 请求：`{"name": string}`。
- `201`：新建的 `Project`。
- 错误：`422`（缺少 `name` 或类型错误）。

### `GET /api/projects`

- 请求：无。
- `200`：按 `created_at` 倒序排列的 `Project[]`；没有项目时为 `[]`。
- 错误：无业务错误。

### `GET /api/projects/{project_id}`

- 请求：路径参数 `project_id`。
- `200`：`Project`。
- 错误：`400`（ID 格式非法）、`404`（项目不存在）。

### `PATCH /api/projects/{project_id}`

- 请求：`BossInfo` 的部分或全部字段：`restaurant_name`、`cuisine_type`、`signature_dishes[]`、`owner_persona`、`audience`、`video_style`、`target_duration_seconds`、`platform`、`hook_preference`。未传字段保留当前值。
- `200`：更新后的 `Project`。
- 错误：`400`、`404`、`422`。

### `GET /api/projects/{project_id}/research`

- `200`：完整 `ResearchProfile`，包含 `store`、`owner`、`audience`、`shooting` 四部分。旧项目没有深度档案时，会从 `boss_info` 生成一份兼容草稿。
- 错误：`400`、`404`。

### `PUT /api/projects/{project_id}/research`

- 请求：完整 `ResearchProfile`。保存时同步更新兼容 `boss_info`，不会覆盖已选脚本。
- `200`：保存后的 `ResearchProfile`。
- 错误：`400`、`404`、`422`。

## 脚本

### `POST /api/projects/{project_id}/script/template`

- 请求：完整 `BossInfo`；字段有默认值，因此允许只传希望覆盖的字段。
- `200`：模板生成并已持久化的 `Script`。
- 错误：`400`、`404`、`422`。

### `GET /api/projects/{project_id}/script`

- 请求：路径参数 `project_id`。
- `200`：`Script`。
- 错误：`400`、`404`（项目不存在或尚未生成脚本，消息会说明原因）。

### `PUT /api/projects/{project_id}/script`

- 请求：完整 `Script`。每个 `shot` 包含 `shot_index`、`lines`、`shooting_tips`、`duration_hint_seconds`，以及可选默认空字符串的 `location`、`angle`。
- `200`：保存后的 `Script`。
- 错误：`400`、`404`、`422`。

### `POST /api/projects/{project_id}/script-bundles/template`

- 请求：`{"research": ResearchProfile, "candidate_count": 3}`；数量允许 2–5。
- `200`：`ScriptBundle`，包含调研摘要、互不重复的候选脚本、适配分、推荐原因、拍摄难度和所需场景。生成方案不会自动覆盖当前脚本。
- 错误：`400`、`404`、`422`。

### `POST /api/projects/{project_id}/script-bundles/ai`

- 请求：`{"research": ResearchProfile, "candidate_count": 3}`；数量允许 2–5。
- `200`：由规则选题、AI 成稿并经程序质检的 `ScriptBundle`。`generator` 为 `ai`，`model_name` 记录实际配置的模型。
- 每个 `shot` 额外包含镜头目的、主体、动作步骤、手机机位、运镜、声音、光线、道具、字幕、剪辑提示、常见错误和重拍条件。
- `502`：模型服务异常或两次输出均未通过校验；`503`：未配置或密钥无效。失败不会覆盖最近一次方案。

### `GET /api/projects/{project_id}/script-bundles/latest`

- `200`：最近生成的 `ScriptBundle`。
- 错误：`400`、`404`（项目或方案不存在）。

### `POST /api/projects/{project_id}/script-bundles/{bundle_id}/select/{script_id}`

- 作用：将候选方案设为当前拍摄脚本，同时在方案集合记录 `selected_script_id`。
- `200`：选中的完整 `Script`，后续素材编号沿用其 1-based 镜头编号。
- 错误：`400`、`404`（方案过期或候选不存在）。

## 素材

### `POST /api/projects/{project_id}/materials`

- 请求：`multipart/form-data`，字段 `shot_index`（应与脚本的 1-based 镜头编号一致；为兼容旧项目仍接受非负整数）与 `file`；扩展名仅支持 `.mp4`、`.mov`、`.mkv`、`.avi`。
- `200`：`{"shot_index", "filename", "duration", "width", "height", "fps", "has_audio"}`。
- 错误：`400`（序号、扩展名或 ffmpeg/ffprobe 处理失败）、`404`（项目不存在）、`409`（该 `shot_index` 已存在）、`422`（表单缺失或类型错误）。

### `GET /api/projects/{project_id}/materials`

- 请求：路径参数 `project_id`。
- `200`：按 `shot_index` 升序排列的素材数组；没有素材时为 `[]`。
- 错误：`400`、`404`。

### `DELETE /api/projects/{project_id}/materials/{shot_index}`

- 请求：非负 `shot_index`。
- `204`：无响应体。
- 错误：`400`（ID 或序号非法）、`404`（项目或素材不存在）、`422`（路径参数不是整数）。

### `GET /api/projects/{project_id}/materials/{shot_index}/thumbnail`

- 请求：非负 `shot_index`。
- `200`：`image/jpeg` 缩略图。
- 错误：`400`、`404`（项目/缩略图不存在）、`422`。

当前版本没有素材原视频流式播放端点；前端只通过缩略图展示素材。

## 时间轴与接缝

`Edits` 为 `{"shots": [{"trim_head", "trim_tail"}], "junctions": [{"transition", "fade_seconds"}]}`。`transition` 可为 `hard | fade | crossfade`；crossfade 会同时交叠视频和音频，并按 `fade_seconds` 缩短总时长。服务端会钳制负裁剪、最短剩余时长及转场时长，并回显生效值。

### `GET /api/projects/{project_id}/edits`

- 请求：路径参数 `project_id`。
- `200`：当前标准化后的 `Edits`；未保存时返回与素材数量匹配的默认值。
- 错误：`400`、`404`。

### `PUT /api/projects/{project_id}/edits`

- 请求：完整或可使用默认空数组的 `Edits`。
- `200`：`{"edits": Edits, "timeline": Timeline}`。
- 错误：`400`、`404`、`422`。

### `GET /api/projects/{project_id}/timeline`

- 请求：路径参数 `project_id`。
- `200`：权威 `Timeline`：`{"segments": [...], "junctions": [...], "total_duration": number}`。无素材时数组为空、总时长为 `0.0`。
- 错误：`400`、`404`。

### `PUT /api/projects/{project_id}/junctions/{junction_index}`

- 请求：`{"trim_tail": number, "trim_head": number, "transition": "hard|fade|crossfade", "fade_seconds": number}`。
- `200`：`{"edits": Edits, "timeline": Timeline}`，均为钳制后的最终值；同时令旧接缝预览缓存失效。
- 错误：`400`（接缝越界）、`404`（项目不存在）、`409`（相邻素材序号不连续）、`422`（请求或路径类型错误）。

### `GET /api/projects/{project_id}/preview/junction/{junction_index}`

- 请求：可选查询参数 `before=1.5`、`after=1.5`（均须大于 0）、`w=360`（120–1080）。
- `200`：`video/mp4` 接缝预览；同一接缝会复用缓存，编辑后缓存失效。
- 错误：`400`、`404`、`409`（接缝越界、素材或渲染失败）、`422`（查询参数非法）。

## 导出与任务

### `POST /api/projects/{project_id}/render/export`

- 请求：路径参数 `project_id`。
- `200`：`{"job_id": string}`；实际渲染在线程中执行。
- 错误：`400`、`404`。素材不足等渲染错误不会使本请求失败，而会写入 job 的 `status=failed` 和中文 `message`。

### `GET /api/jobs/{job_id}`

- 请求：路径参数 `job_id`。
- `200`：`{"status": "pending|running|done|failed", "progress": number, "message": string, "result": object|null}`。成功结果含 `output` 与 `total_duration`。
- 错误：`404`（任务不存在）。

### `GET /api/projects/{project_id}/exports`

- 请求：路径参数 `project_id`。
- `200`：导出目录内的文件名数组，例如 `["final.mp4"]`。
- 错误：`400`、`404`。

### `GET /api/projects/{project_id}/exports/final.mp4`

- 请求：路径参数 `project_id`。
- `200`：附件形式的 `video/mp4`。
- 错误：`400`、`404`（项目或最终文件不存在）。

## AI 润色占位

### `POST /api/projects/{project_id}/polish/junctions/{junction_index}`

- 请求：`{"goal": "harmonize_junction|stabilize|relight|fix_audio", "params": object}`；两个字段均可省略。
- `200`：当前恒为 `{"segment_id": "<project_id>:junction:<index>", "output_file": null, "status": "not_configured", "message": "AI 润色功能尚未接入（二期实现）"}`。
- 错误：`400`（接缝越界）、`404`（项目不存在或相邻素材不完整）、`422`（goal、params 或路径类型错误）。

### `GET /api/projects/{project_id}/polish/providers`

- 请求：路径参数 `project_id`。
- `200`：当前为 `["null"]`，供前端判断能力是否可用。
- 错误：`400`、`404`。

## 非 API 路径

- `GET /docs`：FastAPI Swagger UI。
- `GET /openapi.json`：OpenAPI 描述。
- `GET /`：若 `frontend/dist/index.html` 存在则返回前端 HTML；否则返回 `{"message": "前端未构建，请先运行 npm run build", "docs": "/docs"}`。
- `/assets/*`：构建存在时由 FastAPI 直接托管前端静态资源。所有 API router 都先于 `/` 静态挂载注册，因此 `/api/*` 不受影响。
