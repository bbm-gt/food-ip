已按规格完成 P2 素材上传与时间轴功能，未执行任何 `pip/npm install`，也未修改 `docs/`。

### 实现结果

- 时间轴唯一权威计算与钳制：[timeline.py](C:/Users/HP/food-ip/backend/app/engine/timeline.py:38)
- ffprobe JSON 探测、ffmpeg 回退及缩略图：[media.py](C:/Users/HP/food-ip/backend/app/engine/media.py:107)
- 素材上传、列表、删除、缩略图 API：[materials.py](C:/Users/HP/food-ip/backend/app/api/materials.py:20)
- edits 持久化、服务端钳制、timeline API：[edits.py](C:/Users/HP/food-ip/backend/app/api/edits.py:58)
- 素材路径与项目状态存储：[store.py](C:/Users/HP/food-ip/backend/app/core/store.py:132)
- 三段真实测试视频生成脚本：[make_sample_shots.py](C:/Users/HP/food-ip/backend/scripts/make_sample_shots.py:24)
- 可重复真实 HTTP 冒烟脚本：[e2e_smoke.py](C:/Users/HP/food-ip/backend/scripts/e2e_smoke.py:39)
- 前端素材管理与时间轴页：[App.tsx](C:/Users/HP/food-ip/frontend/src/App.tsx:541)

公式实现：

- hard/fade：不产生重叠，`total = Σd`
- crossfade：累计减去 crossfade 的 `F`
- trim 钳制后始终至少保留 0.5 秒
- `F ∈ [0, min(d_j, d_{j+1}, 1.0)]`
- 混合转场中仅 crossfade 改变 start/total

规格正文公式计算第二个 offset 应为 `12-(0.5+1.0)=10.5`，但测试清单写成了 `10.0`。两者冲突，因此按你特别强调的正文公式实现为 `10.5`。

### 验收结果

1. 后端全量 pytest：通过

真实命令：

```text
backend/.venv/Scripts/python.exe -m pytest backend/app/tests -q
```

真实输出：

```text
........................                                                 [100%]
============================== warnings summary ===============================
backend\.venv\Lib\site-packages\fastapi\testclient.py:1
  C:\Users\HP\food-ip\backend\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
24 passed, 1 warning in 2.61s
```

警告来自现有 FastAPI TestClient，不影响验收，也未按其提示安装依赖。

2. 前端生产构建：通过

PowerShell 禁止执行 `npm.ps1`，因此使用 Windows 等价入口 `npm.cmd run build`：

```text
> food-ip-frontend@0.0.0 build
> tsc && vite build

vite v7.3.6 building client environment for production...
✓ 30 modules transformed.
dist/index.html                  0.40 kB │ gzip:  0.27 kB
dist/assets/index-Dsp7F1Ma.css   8.26 kB │ gzip:  2.44 kB
dist/assets/index-BCq_00gC.js  207.38 kB │ gzip: 65.43 kB
✓ built in 558ms
```

3. 真实 HTTP 冒烟：功能通过；独立 ffprobe 项未验证

真实 uvicorn HTTP 输出：

```json
{
  "project_id": "9e3aa0fad580",
  "probe_backend": "ffmpeg-fallback",
  "uploaded_durations": [6.0, 6.0, 6.0],
  "manual_total_before": 18.0,
  "timeline_total_before": 18.0,
  "timeline_total_after_trim": 16.5,
  "thumbnail_status": 200,
  "thumbnail_content_type": "image/jpeg"
}
```

已验证建项目、上传三段、GET timeline、PUT trim 后更新、再次 GET 持久化一致及缩略图 200。

但本机实际 `config.FFPROBE_PATH=None`，PATH、仓库和常见本机目录均没有 `ffprobe.exe`。因此“独立 ffprobe 手算”无法按字面验证；实现会在 ffprobe 存在时优先使用其 JSON 输出，本次真实冒烟使用现有 ffmpeg 回退，未伪报成 ffprobe。

4. 无新增依赖：通过

`backend/requirements.txt`、`frontend/package.json`、锁文件均无变更；全过程未运行安装命令。规格文件保持原样。

---

## 验收官结论（Claude Code，2026-08-03）

**独立复验全部通过**：

| 验收项 | 结果 | 证据 |
|---|---|---|
| pytest | ✅ 24 passed | 独立运行全量测试 |
| npm build | ✅ | 独立运行 build |
| 真实 HTTP 冒烟 | ✅ | 独立起 uvicorn + httpx(trust_env=False)：建项目→上传3段(6.0s)→timeline total 18.0→PUT trim→16.5→缩略图 200 image/jpeg |
| timeline.py 公式审查 | ✅ | 手动验证 trim 钳制、crossfade offset（6+6−(0.5+1.0)=10.5）、总时长公式，与规格正文一致 |
| 依赖 | ✅ 零新增 | |

**结论：P2 验收通过。** 发现并确认两点：
1. **规格书笔误**：crossfade offset1 应为 10.5（Codex 正确按公式实现，而非测试清单误写的 10.0）——Codex 未盲从规格，属正确行为。
2. **本机无独立 ffprobe**（`FFPROBE_PATH=None`），media.py 用 ffmpeg 回退解析，实测 duration 准确（6.0s）。可接受，MVP 后用 winget 装正式 ffmpeg/ffprobe。

验收时另修复 `backend/scripts/e2e_smoke.py` 两处健壮性问题：httpx 加 `trust_env=False`（本机系统代理会导致 localhost 502）、就绪等待 10s→20s。