# 抖音官方说明同步工具

`backend/scripts/sync_official_guidance.py` 用于低频抓取抖音、巨量引擎和抖音电商学习中心的公开官方页面，把与短视频骨架有关的说明整理成待审核规则。

该工具不会直接修改脚本模板。输出中的候选规则始终带有 `review_status: pending`，需要产品或运营人员确认后，才能人工写入骨架策略库。

## 使用方式

从仓库根目录运行：

```powershell
backend/.venv/Scripts/python.exe backend/scripts/sync_official_guidance.py
```

默认抓取巨量引擎食品饮料行业方案，并写入：

```text
runtime/guidance/official-guidance.json
```

可以追加其他公开的官方页面：

```powershell
backend/.venv/Scripts/python.exe backend/scripts/sync_official_guidance.py `
  --url https://www.oceanengine.com/solution/food-drink `
  --output runtime/guidance/official-guidance.json
```

传入其他页面前，应先确认它是公开可访问的正式说明页，而不是登录页、搜索页或临时活动页。

允许的域名后缀仅包括：

- `douyin.com`
- `oceanengine.com`
- `jinritemai.com`

同时要求 HTTPS、检查 `robots.txt`、限制请求频率，并验证重定向后的最终域名。页面抓取失败时会记录在 `errors`，不会伪造规则。

## 输出字段

- `sources`：来源 URL、标题、内容哈希、更新时间响应头和变更状态。
- `candidate_rules`：按 hook、identity、value、retention、conversion、production 分类的短说明。
- `change_status`：`new`、`changed` 或 `unchanged`。
- `review_required`：恒为 `true`。
- `auto_apply`：恒为 `false`。

脚本只保存短规则候选和正文哈希，不保存完整官方页面副本。这样既能发现官方说明变化，也避免把远程页面内容直接当作生产配置。

## 建议的更新流程

1. 每月或每季度手工运行一次同步工具。
2. 查看 `changed` 来源和新增候选规则。
3. 运营人员确认规则是否适用于餐饮老板 IP，而不只是广告投放。
4. 将通过审核的原则转成骨架评分条件和测试假设。
5. 用实际发布数据验证后，再提高对应骨架的权重。

不建议高频抓取，也不建议让远程页面内容未经审核自动覆盖生产模板。
