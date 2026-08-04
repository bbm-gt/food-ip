# food-ip — 餐饮老板个人 IP 短视频工具

帮餐饮店老板低门槛产出「口播短视频」：脚本生成 → 拍摄 → 自动拼接 → 缝合处调节 →（二期）AI 润色。

## 技术栈

- 后端：Python 3.10+ + FastAPI + uvicorn；`imageio-ffmpeg` 内置静态 ffmpeg，并在缺少 ffprobe 时回退到 ffmpeg 探测
- 前端：Vite + React + TypeScript
- 脚本生成：**独立可插拔模块** —— 模板生成（免费主路径，$0/单，可规模化）+ Codex AI 生成（可选增强，按次付费）+ 手工编辑。AI 不是默认依赖
- 剪辑引擎 100% 在后端（ffmpeg），前端只是 Web 客户端，架构预留手机端

## 核心原则

- `engine/timeline.py` 是**唯一权威时长来源**，前端永不自行算时长，一律 GET `/timeline`
- 一个项目 = `runtime/projects/<id>/` 文件夹（project.json + script.json + shots/ + work/ + exports/ + log/）
- AI 润色只留接口契约（二期接入真实视频模型）
