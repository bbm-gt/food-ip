"""
TikTok 知识库精炼脚本 - 调用 DeepSeek API 精炼合并文件
用法: python refine.py
要求: 设置环境变量 DEEPSEEK_API_KEY，或编辑下方 API_KEY

流程:
  1. 读取 temp_merged/ 下所有合并文件
  2. 调用 DeepSeek 精炼（大文件自动分片 + 合并）
  3. 输出到 refined/ 目录
  4. 支持断点续传（已成功的主题跳过）
"""
import os, sys, json, time, re
from datetime import datetime, timedelta
from pathlib import Path
from food_ip_config import LEGACY_TRANSCRIPTS_DIR

# ── 配置 ──
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = "deepseek-chat"  # DeepSeek V4 Flash
BASE_URL = "https://api.deepseek.com/v1/chat/completions"
TEMPERATURE = 0.3
MAX_TOKENS = 8000  # 单次精炼 max output 上限（约 5000 汉字）
MAX_TOKENS_MERGE = 16000  # 合并多片结果时上限（大主题需要更多空间）
MAX_RETRIES = 3
RETRY_DELAY = [5, 15, 60]

# 大文件分片阈值（字符数）。超过则按 <!-- SOURCE --> 切分多片精炼后合并
CHUNK_THRESHOLD = 25000
CHUNK_SIZE = 20000  # 每片目标字符数

# DeepSeek 峰谷定价（2026 年 7 月中旬起）
# 高峰: 工作日 09:00-12:00, 14:00-18:00 (北京时间 UTC+8)，价格翻倍
# 平价: ¥1/M input, ¥2/M output
PRICE_INPUT_PER_M = 1.0
PRICE_OUTPUT_PER_M = 2.0
PEAK_MULTIPLIER = 2.0


def get_current_price_multiplier():
    """根据当前北京时间判断是否高峰时段，返回价格倍数"""
    # UTC+8 北京时间
    now_bj = datetime.utcnow() + timedelta(hours=8)
    # 周末非高峰
    if now_bj.weekday() >= 5:
        return 1.0
    hour = now_bj.hour
    if (9 <= hour < 12) or (14 <= hour < 18):
        return PEAK_MULTIPLIER
    return 1.0

REFINED_DIR = LEGACY_TRANSCRIPTS_DIR / "refined"
MERGED_DIR = REFINED_DIR / "temp_merged"
OUT_DIR = REFINED_DIR
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 主题 ID 映射
THEME_NAMES = [
    ("01", "跨境入门-市场认知与平台概述"),
    ("02", "选品策略-方法与工具实战"),
    ("03", "内容创作-混剪去重与脚本制作"),
    ("04", "账号运营-起号增长与合规管理"),
    ("05", "达人合作-网红建联与联盟营销"),
    ("06", "广告投放-策略搭建与数据优化"),
    ("07", "独立站-Shopify建站与支付物流"),
    ("08", "TikTok小店-运营管理与订单履约"),
    ("09", "AI工具-数字人与自动化内容生成"),
    ("10", "直播带货-场景搭建与话术策略"),
    ("11", "设备环境-手机配置与网络搭建"),
    ("12", "入驻开店-资质准备与定邀类目"),
    ("13", "综合参考-补充资料与行业数据"),
]

SYSTEM_PROMPT = "你是一位资深的跨境电商知识管理专家，擅长从课程转录文本中提取结构化知识。"

# 精炼 Prompt 模板（用占位符 __CONTENT__ 替代 {content}，避免 .format 误解析内容中的花括号）
REFINE_PROMPT = """你是一位资深的跨境电商知识管理专家。请将以下TikTok跨境电商课程转录文本整理为结构化的知识库文档。

## 输入说明
以下内容是同一主题下多节课程视频的转录文本，通过whisper-large-v3语音识别生成。文本质量参差不齐，包含口语化表达、重复内容、语音识别错误。请提取其中有价值的知识点，忽略无意义的闲聊、自我介绍、课程推广等内容。

## ASR 语音识别错误处理
转录文本由 whisper-large-v3 生成，存在系统性术语误识别。**预处理阶段已修正高频错误**，但仍可能残留未知错误。请按以下原则处理：

### 1. 上下文推理修正
遇到不合上下文、不合逻辑的词，根据跨境电商领域知识推理正确术语。典型示例：
- "1比10的RY" → "1比10的ROI"（投资回报率）
- "JMV目标870亿美金" → "GMV目标870亿美金"
- "开通MSN权限/M3权限" → "开通MCN权限"
- "5ID" → "Apple ID"
- "签粉开通" → "千粉开通"（达到1000粉丝）
- "除窗" → "橱窗"
- "戴火/代货" → "带货"
- "用件/应金" → "佣金"
- "投诚物流/头层物流" → "头程物流"
- "伪成物流/尾层物流" → "尾程物流"
- "本土电/跨境电" → "本土店/跨境店"
- "定要流程" → "定邀流程"
- "续保" → "续绑"（MCN 账号上下文）
- "Shutdown Rocket" → "Shadowrocket"（小火箭工具）
- "ShoppingFi/Shopping fight" → "Shopify"（建站平台）
- "Strip" → "Stripe"（支付网关）
- "Shopper payments" → "Shopify Payments"
- "投赔/走放" → "投放"（广告投放上下文）
- "白面单" → "白名单"（广告白名单系统）
- "受重" → "受众"（广告目标受众）
- "Tito/TITO" → "TikTok"（TikTok 误识别变体）
- "居付率" → "拒付率"（支付风控指标）
- "花经电商" → "跨境电商"

### 2. 常见误识别模式
- **英文缩写被截断或替换**：ROI→RY, GMV→JMV, MCN→MSN/M3/M3N, Apple ID→5ID
- **中文同音字**：橱窗→除窗, 千粉→签粉, 带货→戴火/代货, 佣金→用件/应金, 营业执照→颜值照
- **跨境术语被替换为普通词**：续绑→续保, 头程物流→投诚物流, 本土店→本土电, 入驻→入座
- **品牌名误识别**：Shadowrocket→Shutdown Rocket, Etsy→Estate, Wish→VFL, PayPal→PayEat, Shopify→ShoppingFi/Shopping fight, Stripe→Strip, Shopify Payments→Shopper payments
- **广告投放术语误识别**：投放→投赔/走放, 白名单→白面单, 受众→受重, 计费→积费, 曝光→暴光, 千次展示→签字展示, 时段→时几
- **直播术语误识别**：话术→话速, 提词板→题字版, 话术文档→夸数文章
- **支付术语误识别**：拒付率→居付率, 劣势→列丝
- **TikTok 多种误识别变体**：TikTok→听透/踢到/T-talk/tittle/Tito/TITO
- **实操/运营术语**：测试→测速, 找货→早货, 上传→上转, 运营→运美, 归纳→归关, 介绍→简展, 基础篇→基础片

### 3. 修正原则
- 只修正明显错误的术语，不要过度修改原意
- 数字、百分比保留原始数值（除非明显不合理）
- 英文术语保留原名：ROI、CPM、GMV、Shopify、TikTok Shop、MCN、ACCU、POP、VAT、FBA、FBT、Pixel、ROAS 等
- 不确定的修正可在该处用括号标注，如 "ROI（原转录为RY）"
- 中文专有名词（如"婷姐"、"小黄车"、"葵花宝典"、"独孤九剑"）保持原样

### 4. 不要修正的内容
- 老师故意使用的代号（如"某宝"、"某多多"、"水果手机"指代苹果手机）
- 口语化表达（这是风格约束处理的范围）
- 数字单位（如"5.2K美金"、"52.0K美金"是正确格式）

## 输出要求
请严格按以下6个模块输出Markdown格式文档：

### 1. 【核心观点】
用3-5个要点概括该主题最核心的知识/方法论/洞察。每个要点一句话，用"- "开头的无序列表。

### 2. 【内容标签】
输出3-5个关键词标签，用逗号分隔，用于知识库检索。格式示例：`选品方法, SPY工具, 点赞比分析, 广告素材筛选`

### 3. 【摘要】
150-200字的简明摘要，概括该主题文档覆盖的核心内容和读者能从中获得什么。

### 4. 【FAQ问答对】
针对该主题最常见、最实操的3个问题，以问答形式呈现：
Q: 具体问题（一句话）
A: 详细回答（包含具体方法、数据、步骤，50-100字）

### 5. 【合并优化同类项】
将输入文本中不同文件重复讲解的相同知识点合并去重，按知识点分组呈现。每个知识点一个段落，整合多个来源的互补信息，删除纯重复内容。

### 6. 【提取核心框架】
提取该主题的操作方法论，分为以下子模块：
- **核心观点**：该主题的根本逻辑/原则（几条要点）
- **操作步骤**：如果有实操内容，按1-2-3步骤呈现
- **风险提示**：该主题相关的避坑指南、常见错误、违规风险
- **工具清单**：该主题涉及的工具/平台/资源，用表格呈现（名称 | 用途 | 备注）

## 风格约束
- 不要客套话、不要"很高兴为您服务"等寒暄
- 结论先行，解释在后
- 去除口语化表达，转换为书面教学风格
- 去除个人叙事（"我当时..."、"我记得..."、"我们团队..."）
- 数据、数字、百分比保留原始准确性
- 输出应读起来像个人知识笔记，而非课程推销文案
- 中文输出，专业术语保留英文原名（如ROI、CPM、GMV、Shopify等）
- 不要在输出中出现"本课程"、"在本节中"、"接下来我们"等课程用语

## 输入文本
__CONTENT__"""


# 多片合并 Prompt（当主题过大被切分时，用于整合多个分片精炼结果）
MERGE_PROMPT = """你是一位资深的跨境电商知识管理专家。以下是对同一主题的多个分片分别精炼后的结构化结果。
请把它们整合为一份**详尽完整**的知识库文档。你有 16000 token 的输出空间，请充分展开，不要过度压缩。

遵循以下 6 个模块输出格式：

1. 【核心观点】 — 合并所有分片的核心观点，去重保留 8-12 条最有价值的
2. 【内容标签】 — 合并所有标签，去重保留 6-10 个
3. 【摘要】 — 生成 300-400 字的整合摘要，概括该主题的完整知识体系
4. 【FAQ问答对】 — 合并所有 FAQ，去重保留 5-7 个最高频问题，每个回答要包含具体数据/步骤/案例
5. 【合并优化同类项】 — 跨分片合并重复知识点，每个知识点保留整合后的完整段落（不是一句话概括），含具体方法、数据、工具
6. 【提取核心框架】 — 整合所有分片的操作步骤/风险提示/工具清单，工具表格至少 10 行

风格约束同前：去口语化、去个人叙事、不出现课程用语、保留英文术语。
若发现分片结果中残留 ASR 语音识别错误（如 MSN/M3 应为 MCN、JMV 应为 GMV、5ID 应为 Apple ID、ShoppingFi 应为 Shopify、Strip 应为 Stripe、投赔/走放 应为 投放、白面单 应为 白名单、Tito/TITO 应为 TikTok、居付率 应为 拒付率），请一并修正。

## 待整合的分片结果
__CONTENT__"""


def call_deepseek(prompt_content, model=MODEL, max_tokens=None):
    """调用 DeepSeek API，带重试。max_tokens 覆盖全局默认值。"""
    mt = max_tokens if max_tokens is not None else MAX_TOKENS
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt_content},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": mt,
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    for attempt in range(MAX_RETRIES):
        try:
            import requests
            resp = requests.post(BASE_URL, json=payload, headers=headers, timeout=600)
            if resp.status_code == 200:
                data = resp.json()
                result = data["choices"][0]["message"]["content"]
                # 统计 token 使用
                usage = data.get("usage", {})
                return result, usage
            elif resp.status_code == 429:
                print(f"  ⏳ 限流，等待{RETRY_DELAY[attempt]}秒...")
                time.sleep(RETRY_DELAY[attempt])
            elif resp.status_code in (500, 502, 503):
                print(f"  ⏳ 服务器错误，等待{RETRY_DELAY[attempt]}秒...")
                time.sleep(RETRY_DELAY[attempt])
            elif resp.status_code == 400 and attempt == 0:
                # 可能内容太长，截断到80%
                payload["messages"][1]["content"] = payload["messages"][1]["content"][:int(len(payload["messages"][1]["content"]) * 0.8)]
                print(f"  ⏳ 内容过长，截断到80%重试...")
                continue
            else:
                print(f"  ❌ API错误 {resp.status_code}: {resp.text[:200]}")
                return None, None
        except requests.exceptions.Timeout:
            print(f"  ⏳ 超时，等待{RETRY_DELAY[attempt]}秒...")
            time.sleep(RETRY_DELAY[attempt])
        except Exception as e:
            print(f"  ❌ 异常: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY[attempt])
            else:
                return None, None

    print(f"  ❌ 全部重试失败")
    return None, None


def load_merged_content(theme_id, theme_name):
    """读取合并文件内容"""
    safe_name = theme_name.replace(' ', '').replace('：', '-').replace('，', '-')
    main_file = MERGED_DIR / f"{theme_id}_{safe_name}_merged.txt"
    refined_file = MERGED_DIR / f"{theme_id}_{safe_name}_refined-source.txt"

    content_parts = []

    # 主合并内容
    if main_file.exists():
        content_parts.append(main_file.read_text(encoding='utf-8'))

    # 已精炼源文件（作为参考）
    if refined_file.exists():
        ref_content = refined_file.read_text(encoding='utf-8')
        # 只取其中关键知识部分，不送纯表格数据
        content_parts.append(f"\n\n[预精炼参考内容]\n{ref_content[:3000]}\n")

    return "\n\n".join(content_parts) if content_parts else None


def verify_output(text):
    """验证输出是否包含所有必需模块（与 REFINE_PROMPT 要求的 6 个模块一致）"""
    required = ["【核心观点】", "【内容标签】", "【摘要】", "【FAQ问答对】",
                 "【合并优化同类项】", "【提取核心框架】"]
    missing = [s for s in required if s not in text]
    return missing


def split_content_by_source(content, target_size=CHUNK_SIZE):
    """按 <!-- SOURCE: xxx --> 标记切分大文本为多片，每片尽量接近 target_size 字符。
    保证不切断单个 source 块的内容。"""
    # 按 SOURCE 注释切分
    parts = re.split(r'(<!-- SOURCE: [^>]+ -->)', content)
    chunks = []
    current = ""
    for part in parts:
        if not part.strip():
            continue
        if re.match(r'<!-- SOURCE: [^>]+ -->', part):
            # 如果当前累积已超过 target_size，先收尾
            if len(current) >= target_size:
                chunks.append(current)
                current = ""
            current += part
        else:
            # 内容块；如果加上会超 1.5 倍阈值，则切到下一片
            if current and len(current) + len(part) > target_size * 1.3:
                chunks.append(current)
                current = part
            else:
                current += part
    if current.strip():
        chunks.append(current)
    return chunks


def main():
    if not API_KEY:
        print("❌ 未设置 DEEPSEEK_API_KEY 环境变量")
        print("   请在运行前设置: set DEEPSEEK_API_KEY=your_key_here")
        print("   或在 refine.py 中直接编辑 API_KEY 变量")
        return

    # 检查依赖
    try:
        import requests
    except ImportError:
        print("❌ 缺少 requests 库，请安装: pip install requests")
        return

    # 峰谷定价提示
    price_mult = get_current_price_multiplier()
    if price_mult > 1.0:
        print(f"⚠ 当前为 DeepSeek 高峰时段，API 价格翻倍 ({price_mult}x)")
    else:
        print(f"✓ 当前为 DeepSeek 平价时段")

    total_tokens_in = 0
    total_tokens_out = 0
    total_cost = 0

    results = {}

    for theme_id, theme_name in THEME_NAMES:
        print(f"\n{'='*50}")
        print(f"开始精炼: {theme_id}-{theme_name}")

        out_file = OUT_DIR / f"{theme_id}-{theme_name}.md"

        # 断点续传：已成功输出的主题跳过
        if out_file.exists() and out_file.stat().st_size > 500:
            # 简单校验文件是否包含所有必需模块
            try:
                existing = out_file.read_text(encoding='utf-8')
                if len(verify_output(existing)) == 0:
                    print(f"  ⏭ 已存在且完整，跳过 (断点续传)")
                    results[theme_id] = {"status": "skipped_existing"}
                    continue
                else:
                    print(f"  ⚠ 已存在但模块不全，重新精炼")
            except Exception as e:
                print(f"  ⚠ 读取已有文件失败 ({e})，重新精炼")

        content = load_merged_content(theme_id, theme_name)
        if not content:
            print(f"  ⚠ 无内容，跳过")
            results[theme_id] = {"status": "skipped"}
            continue

        char_count = len(content)
        # DeepSeek tokenizer 中文较友好，约 1 字符 ≈ 0.6-0.7 token；英文 1 字符 ≈ 0.25 token
        # 取 0.7 作为偏保守的中文估算
        est_tokens = int(char_count * 0.7)
        print(f"  内容大小: {char_count//1024}KB (~{est_tokens//1000}K tokens 估算)")

        # 大文件分片处理
        if char_count > CHUNK_THRESHOLD:
            chunks = split_content_by_source(content, CHUNK_SIZE)
            print(f"  📦 内容超过 {CHUNK_THRESHOLD} 字符，切分为 {len(chunks)} 片分别精炼")
            chunk_results = []
            chunk_usages = []
            failed_chunk = False
            for i, chunk in enumerate(chunks, 1):
                print(f"  ─ 片 {i}/{len(chunks)} ({len(chunk)//1024}KB)")
                chunk_prompt = REFINE_PROMPT.replace("__CONTENT__", chunk)
                r, u = call_deepseek(chunk_prompt)
                if r is None:
                    print(f"     ❌ 片 {i} 失败")
                    failed_chunk = True
                    break
                chunk_results.append(r)
                chunk_usages.append(u or {})

            if failed_chunk:
                print(f"  ❌ 分片精炼失败")
                results[theme_id] = {"status": "failed"}
                continue

            # 合并多片结果
            if len(chunk_results) > 1:
                merged_input = "\n\n---\n\n".join(
                    [f"### 分片 {i+1} 精炼结果\n{r}" for i, r in enumerate(chunk_results)]
                )
                print(f"  🔀 合并 {len(chunk_results)} 片结果...")
                merge_prompt = MERGE_PROMPT.replace("__CONTENT__", merged_input)
                result, merge_usage = call_deepseek(merge_prompt, max_tokens=MAX_TOKENS_MERGE)
                if result is None:
                    # 合并失败，退而用最后一片的结果
                    print(f"  ⚠ 合并失败，使用最后一片结果作为兜底")
                    result = chunk_results[-1]
                    merge_usage = chunk_usages[-1]

                # 累计 token：所有分片 + 合并调用
                usage = {"prompt_tokens": 0, "completion_tokens": 0}
                for u in chunk_usages:
                    usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                    usage["completion_tokens"] += u.get("completion_tokens", 0)
                if merge_usage:
                    usage["prompt_tokens"] += merge_usage.get("prompt_tokens", 0)
                    usage["completion_tokens"] += merge_usage.get("completion_tokens", 0)
            else:
                result = chunk_results[0]
                usage = chunk_usages[0]
        else:
            # 单次精炼（用 replace 避免内容含 {} 时 .format 报错）
            full_prompt = REFINE_PROMPT.replace("__CONTENT__", content)
            print(f"  调用 API...")
            result, usage = call_deepseek(full_prompt)

        if result is None:
            print(f"  ❌ 精炼失败")
            results[theme_id] = {"status": "failed"}
            continue

        # 验证输出
        missing = verify_output(result)
        if missing:
            print(f"  ⚠ 缺少模块: {missing}")
            # 严格小于才覆盖：重试结果必须明显更完整才有意义
            if len(missing) >= 2:
                print(f"  🔄 重试一次...")
                is_merge_retry = char_count > CHUNK_THRESHOLD
                retry_prompt = REFINE_PROMPT.replace("__CONTENT__", content) if not is_merge_retry \
                    else MERGE_PROMPT.replace("__CONTENT__", result)
                retry_mt = MAX_TOKENS_MERGE if is_merge_retry else None
                result2, usage2 = call_deepseek(retry_prompt, max_tokens=retry_mt)
                if result2 and len(verify_output(result2)) < len(missing):
                    result = result2
                    if usage2:
                        usage = usage2
                    missing = verify_output(result)
                    print(f"  ✓ 重试后缺失: {missing if missing else '无'}")

        # 统计（含峰谷定价）
        tok_in = usage.get("prompt_tokens", 0) if usage else 0
        tok_out = usage.get("completion_tokens", 0) if usage else 0
        cost = (tok_in / 1_000_000 * PRICE_INPUT_PER_M + tok_out / 1_000_000 * PRICE_OUTPUT_PER_M) * price_mult
        total_tokens_in += tok_in
        total_tokens_out += tok_out
        total_cost += cost

        results[theme_id] = {
            "status": "success",
            "prompt_tokens": tok_in,
            "completion_tokens": tok_out,
            "cost_yuan": round(cost, 4),
            "price_multiplier": price_mult,
            "missing_sections": missing,
            "chunked": char_count > CHUNK_THRESHOLD,
        }

        peak_tag = f" [高峰{price_mult}x]" if price_mult > 1.0 else ""
        print(f"  ✅ 完成 (输入: {tok_in//1000}K, 输出: {tok_out//1000}K, 费用: ¥{cost:.4f}{peak_tag})")

        # 写入输出文件
        out_file.write_text(result, encoding='utf-8')
        print(f"  📄 写入: {out_file.name}")

    # 最终报告
    print(f"\n{'='*50}")
    print(f"精炼完成报告")
    print(f"{'='*50}")
    success = sum(1 for r in results.values() if r.get("status") == "success")
    failed = sum(1 for r in results.values() if r.get("status") == "failed")
    skipped = sum(1 for r in results.values() if r.get("status") == "skipped")
    skipped_existing = sum(1 for r in results.values() if r.get("status") == "skipped_existing")
    print(f"成功: {success} | 失败: {failed} | 跳过(无内容): {skipped} | 跳过(已存在): {skipped_existing}")
    print(f"总输入 tokens: {total_tokens_in:,}")
    print(f"总输出 tokens: {total_tokens_out:,}")
    print(f"总费用: ¥{total_cost:.4f}" + (f" (含高峰时段 {price_mult}x)" if price_mult > 1.0 else " (平价时段)"))

    # 保存报告
    report_path = OUT_DIR / "_refine_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "success": success,
                "failed": failed,
                "skipped_no_content": skipped,
                "skipped_existing": skipped_existing,
                "total_tokens_in": total_tokens_in,
                "total_tokens_out": total_tokens_out,
                "total_cost_yuan": round(total_cost, 4),
                "price_multiplier": price_mult,
            },
            "details": {k: v for k, v in results.items()},
        }, f, ensure_ascii=False, indent=2)
    print(f"\n报告保存至: {report_path}")
    print(f"\n✅ 所有精炼完成! 输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
