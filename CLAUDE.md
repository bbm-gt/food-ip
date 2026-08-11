# food-ip — 餐饮老板个人 IP 短视频工具

帮餐饮店老板低门槛产出「口播短视频」：脚本生成 → 拍摄 → 自动拼接 → 缝合处调节 →（二期）AI 润色。

## 产品方向（2026-08-11 决策）

未来主链：`Owner Input → Intent / Business Objective → confirmed_facts → missing_facts → 少量相关 Memory → 少量 relevant Knowledge → Creative Decision → Writer → Critic → Directed Rewrite → Shoot-ready Script`。旧脚本系统保留为 **Legacy Script Generation = compatibility + baseline + reusable capabilities**，不删除、不再作为未来主架构。**DO NOT build Multi-Agent**；优先 Workflow + structured modules。方向与状态以 `AGENTS.md` / `docs/architecture.md` 为准。

## 技术栈

- 后端：Python 3.10+ + FastAPI + uvicorn；`imageio-ffmpeg` 内置静态 ffmpeg，并在缺少 ffprobe 时回退到 ffmpeg 探测
- 前端：Vite + React + TypeScript
- 脚本生成：**独立可插拔模块** —— 规则选题 + DeepSeek 生成 + 程序硬校验为主路径（默认三套候选），模板生成保留为兼容/兜底入口 + 手工编辑；AI 编导审稿为独立只读质量层；AI 局部修稿已实现为独立函数（未自动接入生成流程）
- 剪辑引擎 100% 在后端（ffmpeg），前端只是 Web 客户端，架构预留手机端

## 核心原则

- `engine/timeline.py` 是**唯一权威时长来源**，前端永不自行算时长，一律 GET `/timeline`
- 一个项目 = `runtime/projects/<id>/` 文件夹（project.json + script.json + shots/ + work/ + exports/ + log/）
- AI 润色只留接口契约（二期接入真实视频模型）
