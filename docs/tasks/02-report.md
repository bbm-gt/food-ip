已按规格书完成 P1 脚本生成模块。

主要实现：

- 后端 BossInfo、Shot、ScriptModel 数据模型。
- 确定性 6 镜头模板生成器，覆盖川菜、火锅、烧烤、家常菜及通用 fallback。
- Codex 生成器占位与 registry。
- 安全的项目文件存储、`project.json` / `script.json` 同步。
- 项目与脚本 REST API，中文 404 信息。
- 前端项目列表、引导问卷、脚本展示编辑与保存。
- 增加生成器、API、路径安全和端到端测试。

关键文件：[template.py](C:/Users/HP/food-ip/backend/app/scriptgen/generators/template.py)、[store.py](C:/Users/HP/food-ip/backend/app/core/store.py)、[App.tsx](C:/Users/HP/food-ip/frontend/src/App.tsx)。

### 验收结果

1. 后端 pytest：通过，11 项全部成功。

真实执行命令：

```text
backend/.venv/Scripts/python.exe -m pytest backend/app/tests -q
```

真实输出：

```text
...........                                                              [100%]
============================== warnings summary ===============================
backend\.venv\Lib\site-packages\fastapi\testclient.py:1
  C:\Users\HP\food-ip\backend\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
11 passed, 1 warning in 0.41s
```

2. 前端构建：通过。

PowerShell 的 `npm.ps1` 被本机执行策略拦截，因此通过 `cmd.exe /d /c npm run build` 执行同一构建脚本。`tsc && vite build` 成功，30 个模块完成转换，构建耗时 556ms。

3. 独立 TestClient 端到端验收：通过。

```text
create_status=201
generate_status=200
shots=6
duration_sum=60
persisted=True
```

4. 依赖：未新增。未执行任何 `pip install` 或 `npm install`。pytest 中的 `httpx2` 提示仅为现有依赖的弃用警告，不影响验收，也未据此安装依赖。

5. 改动范围：`git diff --check` 通过；依赖清单和已跟踪的 `docs/` 文件无差异；未提交或推送远程。工作树中的未跟踪文档保持原样。

---

## 验收官结论（Claude Code，2026-08-03）

**独立复验全部通过**：

| 验收项 | 结果 | 证据 |
|---|---|---|
| pytest | ✅ 11 passed | 独立运行 `pytest backend/app/tests -q` |
| npm build | ✅ | 独立运行 `npm run build`（30 modules） |
| 真实 HTTP 冒烟 | ✅ | 起 uvicorn 实测：health 200 / 建项目 201 / 模板生成 200（6镜头）/ 时长总和60s / GET 持久化 / PUT 手工编辑 200 |
| 代码审查 | ✅ | template.py（确定性6镜头+品类模板+时长分配）、store.py（路径安全+uuid+Pydantic）、api 路由、异常处理均干净正确 |
| 依赖 | ✅ 未新增 | 无 pip/npm 安装 |

**结论：P1 验收通过。** 未发现幻觉（测试/冒烟/审查三方证据一致）。