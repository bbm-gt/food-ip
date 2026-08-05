# 待办任务（Next Tasks）

> 记录日期：2026-08-05。汇总 T111（Creative 流程接入前端）及后续修复后的剩余任务，供下次接手按优先级处理。
>
> 本轮已完成并提交（按顺序）：
> - `daf2412` 修复用户可见错误信息乱码
> - `c4c231e` 前端接入 IP 定位 + AI 共创流程（含 useCreativeFlow hook）
> - `0050e56` AI 脚本 CTA 目标感知 + 质检失败 template_fallback 兜底
> - `9aafdce` 规则路径 CTA 文案与 AI 路径对齐、去除禁用表达
> - `209d6aa` 话术校验改为上下文约束（放行开场「先别划走」、结尾「收藏/报到」，保留夸大词硬禁）

---

## P0 ｜ 代码一致性

### 1. `/script-bundles/ip-ai` 缺少质检兜底
- 现状：`backend/app/api/script.py` 的 `generate_ip_script_bundle_route` 仍只 `except AIScriptError` → 502；`AIResponseError`（质检失败）没有走 `template_fallback`。
- 对比：普通 `/script-bundles/ai` 已实现兜底（`0050e56`）。
- ⚠️ 注意：兜底使用规则模板，会**丢弃 IP 定位上下文**。需与用户确认是否要与普通路径保持一致。

## P0 ｜ 文档更新（当前文档已失真）

### 2. 修正 `docs/api.md` 中 `/script-bundles/ai` 的 502 语义
- 第 105 行目前写：`502：模型服务异常或两次输出均未通过校验；503：未配置或密钥无效。失败不会覆盖最近一次方案。`
- 实际现状：
  - 两次输出未通过校验 → **200 + `generator=template_fallback`**（并写入 `script_bundle.json`，覆盖最近一次方案）；
  - 仅服务异常（`AIServiceError`：超时/连接/429/截断）→ 502；
  - 未配置/密钥无效（`AIConfigurationError`）→ 503。

### 3. 重写 `HANDOFF.md`
- 目前严重过时：
  - 第 38 行还写「脚本生成：纯规则模板（确定性，零 LLM）」
  - 第 99 行把 IP 定位 / AI 共创列为「v2 路线图（未实现）」
- 需更新到真实状态：DeepSeek 为主路径、IP 定位 / AI 共创已实现、T111 前端已接入、目标感知 CTA、上下文话术校验、质检失败 template_fallback。

---

## P1 ｜ 端到端确认

### 4. 真实环境触发一次 fallback
- 构造必失败的 AI 输出场景（例如多次用弱提示），用真实 DeepSeek 确认 `/script-bundles/ai` 兜底在实跑中生效。
- 单元测试已覆盖逻辑（`test_ai_quality_failure_falls_back_to_template`），本项是补一次实跑确认。

---

## P2 ｜ 基础设施（较大，建议另起任务）

### 5. 前端自动化测试
- 当前前端只有 `npm run build`，无单测。
- 建议引入 vitest，覆盖 `workflow.ts` 恢复逻辑、`useCreativeFlow` handler、`api/client.ts`。

### 6. 环境整理
- 安装正式 ffprobe（当前用 imageio-ffmpeg + ffmpeg 回退探测，实测准确但非正式）。
- 清理 `runtime/projects/` 下 9 个遗留旧项目（动旧数据前需用户确认）。

### 7. 二期方向（明确不在本次范围）
- AI 视频润色真实 provider 接入（当前仅 `null` provider，恒 `not_configured`）。
- 云端部署 / 手机端适配（`docs/deploy.md` 有思路）。
