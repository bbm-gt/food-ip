# 部署说明

## 本地开发

从仓库根目录启动后端：

```powershell
backend/.venv/Scripts/python.exe -m uvicorn backend.app.main:app --reload --port 8000
```

另开终端启动前端开发服务器：

```powershell
cd frontend
npm run dev
```

访问 <http://localhost:5173>。Vite 将 `/api` 代理到 `127.0.0.1:8000`；后端默认通过 `CORS_ORIGINS=http://localhost:5173` 允许该来源。运行数据写入 `PROJECTS_ROOT`，默认是 `runtime/projects`。

## 单进程部署

先生成前端产物，再启动 FastAPI：

```powershell
cd frontend
npm run build
cd ..
backend/.venv/Scripts/python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

FastAPI 启动时检查 `FRONTEND_DIST/index.html`（默认 `frontend/dist/index.html`）：

- 存在时，先托管 `/assets`，再把 `/` 挂到前端构建目录；`/api`、`/docs` 和 `/openapi.json` 已提前注册，优先匹配。
- 不存在时，`GET /` 返回中文构建提示，API 和 Swagger 仍可正常使用。

部署时应把 `PROJECTS_ROOT` 指向可持久化且可写的磁盘目录，并保证服务账号能执行 ffmpeg/ffprobe。`DIRECTOR_DB_PATH` 必须指向可写持久卷中的 SQLite 文件；启动时会初始化并校验 Director 六表。不要把临时容器文件系统当成 Director 数据持久层。

新产品默认 `DIRECTOR_STAGE_MODE=semantic_only`。`legacy` 仅用于显式兼容回退。Director 模型使用独立的 `DIRECTOR_DEEPSEEK_*` 配置，不能依赖 `AI_SCRIPT_*`；`DIRECTOR_CONTEXT_MAX_UNITS` 和 `DIRECTOR_MAX_INTERNAL_STEPS` 分别控制上下文预算与单 Turn 后台推进上限。

当前 job 状态保存在进程内存中，因此重启会丢失未完成任务的查询记录；项目、Director Session、素材和已导出文件不会丢失（前提是各自路径位于持久卷）。

建议由反向代理提供 HTTPS、请求体大小限制和访问控制。生产环境按真实域名收窄 `CORS_ORIGINS`，不要使用任意来源。

## 验证

```powershell
curl.exe -i http://127.0.0.1:8000/
curl.exe -i http://127.0.0.1:8000/api/health
```

构建存在时，第一个请求应为 HTML；第二个请求应始终为 `200` 且 JSON 中 `ok` 为 `true`。

## 未来云端部署

后续可以把同一 FastAPI 应用搬到云服务器或容器平台，并把 `PROJECTS_ROOT` 迁移到持久卷/对象存储。需要同步改造的部分包括：

- 将内存 job 注册表换成持久队列，渲染任务由独立 worker 执行；
- 对上传、预览和导出文件增加鉴权、配额、生命周期与对象存储 URL；
- 固定 ffmpeg 版本并配置 CPU、磁盘空间和超时监控；
- 为前端补充移动端拍摄/上传适配、弱网重试和更明确的进度恢复；
- 真实 AI 润色继续通过 provider 接口接入，不改剪辑引擎。

当前仓库不包含云厂商专用配置，也没有接入真实 AI 润色服务。
