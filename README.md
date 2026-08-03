# Food IP Studio

Food IP Studio 是一个面向餐饮老板的短视频生产工具，用深度调研建立门店与老板 IP 档案，生成多套可选拍摄脚本，并把实拍素材剪成可下载的竖屏成片。

当前 MVP 流程：

```text
五步深度调研 → AI 生成并质检三套脚本 → 详细分镜指导 → 素材拼接 → 接缝调节 → 导出成片
```

脚本主路径使用“规则选题 + DeepSeek 生成 + 程序质检”：规则负责事实与拍摄边界，AI 负责连贯表达和详细导演指导，默认返回三套方向不同的方案。旧版模板接口继续保留用于兼容已有项目；视频成片润色仍为后续能力。

素材镜头编号与脚本保持一致（默认从 1 开始）；删除中间素材后，下一次上传会优先补齐缺失编号。转场支持硬切、淡入淡出和真正的音视频 crossfade。

## 快速开始

要求：Python 3.10+、Node.js/npm，以及可用的 ffmpeg/ffprobe。下列命令均从仓库根目录执行。

### 后端

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
backend/.venv/Scripts/python.exe -m uvicorn backend.app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/docs> 查看 OpenAPI 页面。若前端尚未构建，访问 `/` 会返回带构建提示的 JSON。

### 前端开发模式

```powershell
cd frontend
npm install
npm run dev
```

Vite 默认运行在 <http://localhost:5173>，开发服务器把 `/api` 代理到 <http://127.0.0.1:8000>。

### 单进程生产模式

```powershell
cd frontend
npm run build
cd ..
backend/.venv/Scripts/python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

构建后，FastAPI 从 `frontend/dist` 托管首页与 `/assets` 静态文件；API 仍位于 `/api`，整个应用只需一个服务进程。

## 环境变量

可复制 `.env.example` 为仓库根目录的 `.env`。进程环境优先于 `.env`，多个 CORS 来源用英文逗号分隔。

| 变量 | 默认值/作用 |
|---|---|
| `CODEX_BIN` | 默认从 `PATH` 查找 `codex`；也可填写绝对路径，当前仅为后续可选脚本增强预留 |
| `PROJECTS_ROOT` | `<仓库根>/runtime/projects`；项目 JSON、素材、预览和导出的持久化目录 |
| `CORS_ORIGINS` | `http://localhost:5173`；允许跨域访问 API 的前端来源列表 |
| `FRONTEND_DIST` | `<仓库根>/frontend/dist`；可选覆盖生产前端构建目录 |
| `DEEPSEEK_API_KEY` | DeepSeek 密钥；只写入本机 `.env`，前端和接口响应不会返回该值 |
| `AI_SCRIPT_BASE_URL` | 默认 `https://api.deepseek.com`；更换兼容服务时修改 |
| `AI_SCRIPT_MODEL` | 默认 `deepseek-v4-flash`；后续换模型无需修改业务代码 |
| `AI_SCRIPT_THINKING` | 默认 `disabled`，降低延迟与费用；可改为 `enabled` |
| `AI_SCRIPT_TIMEOUT_SECONDS` | 默认 `90`；单次模型请求超时秒数 |

## 测试

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/app/tests -q
```

更多说明见 [深度调研与多脚本设计](docs/questionnaire-design.md)、[REST API 契约](docs/api.md)、[部署说明](docs/deploy.md)、[AI 润色接口](docs/polish-interface.md) 和 [抖音官方说明同步工具](docs/official-guidance-sync.md)。
