已严格按任务 04 完成核心渲染阶段。

### 验收结果

- 通过：主渲染 `filter_complex`、硬切/淡入淡出、静音轨预处理、1080×1920/30fps/H.264/AAC。[build.py](C:/Users/HP/food-ip/backend/app/engine/build.py:32)
- 通过：接缝低清预览、服务端时长钳制、预览缓存失效。[junction.py](C:/Users/HP/food-ip/backend/app/engine/junction.py:19)
- 通过：线程安全 job、FFmpeg `out_time_us` 进度解析、后台导出与下载。[export.py](C:/Users/HP/food-ip/backend/app/engine/export.py:15) / [jobs.py](C:/Users/HP/food-ip/backend/app/core/jobs.py:14)
- 通过：junction、preview、export、jobs、exports 全部 API。[edits.py](C:/Users/HP/food-ip/backend/app/api/edits.py:78) / [render.py](C:/Users/HP/food-ip/backend/app/api/render.py:22)
- 通过：前端 edit/export 视图、400ms 预览刷新、进度轮询、下载入口。[App.tsx](C:/Users/HP/food-ip/frontend/src/App.tsx:779)
- 通过：所有时长均取自 `timeline.py` 的 segment/total_duration。
- 通过：未修改依赖文件，未执行 pip/npm install，未修改 docs。

`crossfade` 本期没有实现交叠渲染；历史值按 fade 视觉处理，并以 `timeline.total_duration` 限制最终输出时长。

### 真实 pytest 输出

使用指定解释器运行；仅将 `TEMP/TMP` 指向工作区，避开沙箱对系统临时目录的清理权限限制。

```text
.............................                                            [100%]
============================== warnings summary ===============================
backend\.venv\Lib\site-packages\fastapi\testclient.py:1
  C:\Users\HP\food-ip\backend\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
29 passed, 1 warning in 7.37s
```

### 其他验收

- `npm.cmd run build`：通过，TypeScript 与 Vite 构建成功。
- 真实 uvicorn 三素材冒烟：通过。
  - 接缝预览：200，3.0 秒
  - 导出进度：0 → 6 → 24 → 43 → 61 → 82 → 99 → 100
  - timeline：17.5 秒
  - 成片探测：17.5 秒，误差 0
  - 下载：200，文件大小 11,131,412 字节
- 新增真实渲染测试：[test_build.py](C:/Users/HP/food-ip/backend/app/tests/test_build.py:67)、[test_junction_api.py](C:/Users/HP/food-ip/backend/app/tests/test_junction_api.py:25)、[test_export_api.py](C:/Users/HP/food-ip/backend/app/tests/test_export_api.py:15)。

---

## 验收官结论（Claude Code，2026-08-03）

**独立复验全部通过**：

| 验收项 | 结果 | 证据 |
|---|---|---|
| pytest | ✅ 29 passed | 独立运行全量测试（含 3 个真实渲染测试） |
| npm build | ✅ | 独立运行 |
| 真实 HTTP 冒烟 | ✅ | 独立起 uvicorn + httpx(trust_env=False)：上传3段→timeline 16.5s→预览 mp4 200→导出 done(progress 100)→成片下载 10.8MB |
| **成片时长一致性（命门）** | ✅ | 成片 probe 时长 **16.5s = timeline 16.5s，零误差**；1080×1920 / 29.88fps / 有音频 |
| build.py 代码审查 | ✅ | filter_complex 结构（trim/scale/pad/fps/format + fade in/out + apad + concat）、out_time_us 进度解析、缺音轨补静音，均符合规格 |
| 依赖 | ✅ 零新增 | |

**结论：P3 验收通过。** crossfade 按规格本期不渲染（UI 只提供硬切/淡入淡出），历史 crossfade 值按 fade 视觉处理并以 timeline.total 封顶，已注明。