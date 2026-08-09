"""
ASR 错误自动挖掘脚本 - 用 LLM 从转录文件中批量发现 ASR 误识别
用法:
  python mine_glossary.py mine [--limit N] [--filter "关键词"]  # 挖掘模式
  python mine_glossary.py merge                                  # 审核并合并到术语表
  python mine_glossary.py status                                 # 查看进度

原理:
  1. 读取 transcripts/ 下每个 .md 文件
  2. 发送给 DeepSeek，让 LLM 识别 ASR 错误（wrong→right）
  3. 过滤掉已知错误，输出候选到 asr_candidates.jsonl
  4. merge 模式：人工审核候选，批准的合并进 asr_glossary.json

优势:
  - 可扩展到 200+ 文件，无需手动阅读
  - LLM 利用领域知识发现人类可能遗漏的错误
  - 人工审核环节（用户作为决策者）
  - 增量处理：已处理的文件跳过
"""
import os, sys, json, time, re, argparse
from datetime import datetime, timedelta
from pathlib import Path

# ── 配置 ──
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = "deepseek-chat"
BASE_URL = "https://api.deepseek.com/v1/chat/completions"
TEMPERATURE = 0.1  # 低温度，提高一致性
MAX_TOKENS = 4000
MAX_RETRIES = 3
RETRY_DELAY = [5, 15, 60]

TRANSCRIPTS_DIR = Path(r"E:\video_transcripts\transcripts")
GLOSSARY_PATH = Path(__file__).parent / "asr_glossary.json"
CANDIDATES_PATH = Path(__file__).parent / "asr_candidates.jsonl"
STATE_PATH = Path(__file__).parent / "asr_mine_state.json"

# DeepSeek 峰谷定价
PRICE_INPUT_PER_M = 1.0
PRICE_OUTPUT_PER_M = 2.0
PEAK_MULTIPLIER = 2.0


def get_current_price_multiplier():
    now_bj = datetime.utcnow() + timedelta(hours=8)
    if now_bj.weekday() >= 5:
        return 1.0
    hour = now_bj.hour
    if (9 <= hour < 12) or (14 <= hour < 18):
        return PEAK_MULTIPLIER
    return 1.0


def load_glossary():
    """加载现有术语表，返回已知 wrong 集合"""
    try:
        data = json.loads(GLOSSARY_PATH.read_text(encoding='utf-8'))
        wrongs = {item["wrong"] for item in data.get("replacements", [])}
        return wrongs, data
    except Exception as e:
        print(f"⚠ 加载术语表失败: {e}")
        return set(), {}


def load_state():
    """加载处理状态（已处理的文件列表）"""
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding='utf-8'))
        except:
            pass
    return {"processed_files": [], "total_cost": 0, "last_run": ""}


def save_state(state):
    state["last_run"] = datetime.now().isoformat()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


MINE_PROMPT = """你是一位跨境电商领域的 ASR（语音识别）错误检测专家。请分析以下 TikTok 跨境电商课程转录文本，找出 whisper-large-v3 语音识别产生的术语错误。

## 任务
逐句阅读转录文本，识别被错误识别的术语。常见错误类型：
1. **英文品牌名/缩写被误识别**：如 Shopify→ShoppingFi, Stripe→Strip, TikTok→Tito/听透, MCN→MSN/M3
2. **中文同音字导致术语错误**：如 曝光→暴光, 受众→受重, 计费→积费, 拒付率→居付率
3. **跨境领域术语被替换为普通词**：如 投放→投赔/走放, 白名单→白面单, 话术→话速
4. **支付/物流/广告领域专有词误识别**：如 Stripe→Strip, Shopify Payments→Shopper payments

## 已知错误（不要重复报告这些）
__KNOWN_ERRORS__

## 识别标准
- 只报告**跨境电商领域术语**的误识别，不报告口语化表达或普通错别字
- 错误词在上下文中明显不合理（如"投赔费用"在广告投放语境中应为"投放费用"）
- 每个错误必须能根据上下文推断出正确词
- confidence: high（明显错误）/ medium（很可能错误）/ low（不确定）

## 输出格式
严格输出 JSON 数组，不要有其他文字。如果没发现新错误，输出空数组 []。

```json
[
  {
    "wrong": "错误词",
    "right": "正确词",
    "category": "类别",
    "context": "错误词出现的原文句子片段（限50字）",
    "confidence": "high"
  }
]
```

类别可选：MCN/TikTok/Shadowrocket/小黄车/带货/粉丝/佣金/橱窗/流量/转化率/选品/涨粉/字节/邮箱/社交/设备/whoer/老师称呼/查重/实操/地区/剪辑/广告/直播/独立站/支付/其他

## 转录文本
__CONTENT__"""


def call_deepseek(prompt):
    """调用 DeepSeek API"""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    import requests
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(BASE_URL, json=payload, headers=headers, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                result = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return result, usage
            elif resp.status_code == 429:
                print(f"    ⏳ 限流，等待{RETRY_DELAY[attempt]}秒...")
                time.sleep(RETRY_DELAY[attempt])
            elif resp.status_code in (500, 502, 503):
                print(f"    ⏳ 服务器错误，等待{RETRY_DELAY[attempt]}秒...")
                time.sleep(RETRY_DELAY[attempt])
            else:
                print(f"    ❌ API错误 {resp.status_code}: {resp.text[:200]}")
                return None, None
        except requests.exceptions.Timeout:
            print(f"    ⏳ 超时，等待{RETRY_DELAY[attempt]}秒...")
            time.sleep(RETRY_DELAY[attempt])
        except Exception as e:
            print(f"    ❌ 异常: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY[attempt])
            else:
                return None, None
    print(f"    ❌ 全部重试失败")
    return None, None


def parse_candidates(response_text):
    """解析 LLM 返回的 JSON 候选列表"""
    # response_format json_object 会包裹在 {"key": [...]} 中，或直接是数组
    try:
        data = json.loads(response_text)
        if isinstance(data, list):
            return data
        # 可能包裹在某个 key 下
        for key in data:
            if isinstance(data[key], list):
                return data[key]
        return []
    except json.JSONDecodeError:
        # 尝试提取 JSON 数组
        match = re.search(r'\[.*\]', response_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                return []
        return []


def mine_mode(args):
    """挖掘模式：扫描转录文件，发现 ASR 错误"""
    if not API_KEY:
        print("❌ 未设置 DEEPSEEK_API_KEY 环境变量")
        print("   请在运行前设置: set DEEPSEEK_API_KEY=your_key_here")
        return

    try:
        import requests
    except ImportError:
        print("❌ 缺少 requests 库，请安装: pip install requests")
        return

    known_wrongs, _ = load_glossary()
    print(f"📋 已加载术语表：{len(known_wrongs)} 条已知错误")

    state = load_state()
    processed = set(state.get("processed_files", []))

    # 收集待处理文件
    md_files = sorted(TRANSCRIPTS_DIR.glob("*.md"))
    if args.filter:
        md_files = [f for f in md_files if args.filter in f.name]
    pending = [f for f in md_files if f.name not in processed]

    if args.limit:
        pending = pending[:args.limit]

    print(f"📁 转录文件总数: {len(md_files)}")
    print(f"✅ 已处理: {len(processed)}")
    print(f"⏳ 待处理: {len(pending)}")

    if not pending:
        print("🎉 所有文件已处理完毕！请运行 'python mine_glossary.py merge' 审核候选。")
        return

    # 峰谷定价提示
    price_mult = get_current_price_multiplier()
    if price_mult > 1.0:
        print(f"⚠ 当前为 DeepSeek 高峰时段，API 价格翻倍 ({price_mult}x)")
        print(f"   建议在平价时段运行（工作日 12:00-14:00, 18:00 后, 或周末）")
    else:
        print(f"✓ 当前为 DeepSeek 平价时段")

    # 已知错误列表（截取前 150 条，避免 prompt 过长）
    known_list = sorted(known_wrongs, key=len, reverse=True)[:150]
    known_str = "、".join(known_list)

    total_new = 0
    total_cost = state.get("total_cost", 0)

    for i, md_file in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}] {md_file.name}")

        content = md_file.read_text(encoding='utf-8')
        # 截取正文（跳过 YAML front matter），限制长度避免 token 过多
        body_start = content.find('---', content.find('---') + 3)
        if body_start > 0:
            body = content[body_start + 3:]
        else:
            body = content
        # 限制到 15000 字符（约 10K tokens）
        if len(body) > 15000:
            body = body[:15000] + "\n...[截断]"

        prompt = MINE_PROMPT.replace("__KNOWN_ERRORS__", known_str)
        prompt = prompt.replace("__CONTENT__", body)

        result, usage = call_deepseek(prompt)
        if result is None:
            print(f"    ⚠ 跳过（API 失败）")
            continue

        # 统计费用
        tok_in = usage.get("prompt_tokens", 0) if usage else 0
        tok_out = usage.get("completion_tokens", 0) if usage else 0
        cost = (tok_in / 1_000_000 * PRICE_INPUT_PER_M + tok_out / 1_000_000 * PRICE_OUTPUT_PER_M) * price_mult
        total_cost += cost

        # 解析候选
        candidates = parse_candidates(result)
        # 过滤已知错误
        new_candidates = []
        for c in candidates:
            wrong = c.get("wrong", "").strip()
            if not wrong or wrong in known_wrongs:
                continue
            c["source_file"] = md_file.name
            c["mined_at"] = datetime.now().isoformat()
            new_candidates.append(c)

        # 追加到候选文件
        if new_candidates:
            with open(CANDIDATES_PATH, "a", encoding="utf-8") as f:
                for c in new_candidates:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")

        total_new += len(new_candidates)
        peak_tag = f" [高峰{price_mult}x]" if price_mult > 1.0 else ""
        print(f"    ✅ 发现 {len(new_candidates)} 个新候选 (输入:{tok_in} 输出:{tok_out} 费用:¥{cost:.4f}{peak_tag})")
        for c in new_candidates[:3]:
            print(f"       {c['wrong']} → {c['right']} [{c.get('category','?')}] ({c.get('confidence','?')})")
        if len(new_candidates) > 3:
            print(f"       ... 还有 {len(new_candidates)-3} 条")

        # 更新状态
        state["processed_files"] = list(processed | {md_file.name})
        state["total_cost"] = total_cost
        save_state(state)

        # 间隔，避免限流
        if i < len(pending):
            time.sleep(1)

    # 汇总
    print(f"\n{'='*50}")
    print(f"挖掘完成")
    print(f"{'='*50}")
    print(f"处理文件: {len(pending)}")
    print(f"发现新候选: {total_new}")
    print(f"本次费用: ¥{total_cost - state.get('total_cost', 0):.4f}")
    print(f"累计费用: ¥{total_cost:.4f}")
    print(f"\n候选文件: {CANDIDATES_PATH}")
    print(f"请运行 'python mine_glossary.py merge' 审核并合并候选到术语表")


def merge_mode(args):
    """合并模式：审核候选并合并到术语表"""
    if not CANDIDATES_PATH.exists():
        print("❌ 无候选文件。请先运行 'python mine_glossary.py mine'")
        return

    # 读取所有候选
    candidates = []
    with open(CANDIDATES_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    candidates.append(json.loads(line))
                except:
                    pass

    if not candidates:
        print("❌ 候选文件为空")
        return

    # 按出现频次聚合
    from collections import Counter
    wrong_counter = Counter(c["wrong"] for c in candidates)
    # 去重：同一 wrong 取第一个
    seen = set()
    unique = []
    for c in candidates:
        if c["wrong"] not in seen:
            seen.add(c["wrong"])
            c["count"] = wrong_counter[c["wrong"]]
            unique.append(c)

    # 按频次降序
    unique.sort(key=lambda x: (-x["count"], x.get("confidence", ""), x["wrong"]))

    _, glossary = load_glossary()
    existing_wrongs = {item["wrong"] for item in glossary.get("replacements", [])}

    print(f"📋 候选总数: {len(candidates)} 条")
    print(f"📋 去重后: {len(unique)} 条")
    print(f"📋 已在术语表: {len(existing_wrongs)} 条")
    print(f"\n以下为候选列表（按频次降序）。请逐条审核：")
    print(f"  y = 批准合并   n = 拒绝   s = 跳过   a = 批准全部   q = 退出")
    print(f"{'='*60}")

    approved = []
    rejected = []

    for i, c in enumerate(unique, 1):
        if c["wrong"] in existing_wrongs:
            continue  # 已存在，跳过

        print(f"\n[{i}/{len(unique)}] 频次:{c['count']} 置信度:{c.get('confidence','?')}")
        print(f"  wrong: {c['wrong']}")
        print(f"  right: {c['right']}")
        print(f"  category: {c.get('category', '?')}")
        print(f"  context: {c.get('context', '?')}")
        print(f"  source: {c.get('source_file', '?')}")

        choice = input("  >> (y/n/s/a/q): ").strip().lower()
        if choice == 'y':
            approved.append(c)
        elif choice == 'n':
            rejected.append(c)
        elif choice == 'a':
            approved.append(c)
            approved.extend(cc for cc in unique[i:] if cc["wrong"] not in existing_wrongs and cc["wrong"] not in {a["wrong"] for a in approved})
            break
        elif choice == 'q':
            break
        # 's' or other = skip

    if not approved:
        print("\n未批准任何候选。退出。")
        return

    # 合并到术语表
    for c in approved:
        entry = {
            "wrong": c["wrong"],
            "right": c["right"],
            "category": c.get("category", "其他"),
        }
        glossary["replacements"].append(entry)
        # 同步黑名单
        if c["wrong"] not in glossary.get("blacklist", []):
            glossary.setdefault("blacklist", []).append(c["wrong"])

    # 更新 meta
    glossary["_meta"]["updated"] = datetime.now().strftime("%Y-%m-%d")

    # 写回
    GLOSSARY_PATH.write_text(
        json.dumps(glossary, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    print(f"\n{'='*50}")
    print(f"合并完成")
    print(f"{'='*50}")
    print(f"批准: {len(approved)} 条")
    print(f"拒绝: {len(rejected)} 条")
    print(f"术语表现在: {len(glossary['replacements'])} 条替换规则")
    print(f"黑名单: {len(glossary['blacklist'])} 条")

    # 清理已处理的候选
    remaining = [c for c in candidates if c["wrong"] not in {a["wrong"] for a in approved}]
    with open(CANDIDATES_PATH, "w", encoding="utf-8") as f:
        for c in remaining:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"候选文件剩余: {len(remaining)} 条（已合并的已移除）")


def status_mode(args):
    """查看进度"""
    state = load_state()
    _, glossary = load_glossary()

    md_files = list(TRANSCRIPTS_DIR.glob("*.md"))
    processed = set(state.get("processed_files", []))
    pending = [f.name for f in md_files if f.name not in processed]

    # 候选统计
    candidate_count = 0
    if CANDIDATES_PATH.exists():
        with open(CANDIDATES_PATH, "r", encoding="utf-8") as f:
            candidate_count = sum(1 for line in f if line.strip())

    print(f"=== ASR 错误挖掘进度 ===")
    print(f"转录文件总数: {len(md_files)}")
    print(f"已处理: {len(processed)}")
    print(f"待处理: {len(pending)}")
    print(f"术语表: {len(glossary.get('replacements', []))} 条替换规则")
    print(f"候选文件: {candidate_count} 条待审核")
    print(f"累计费用: ¥{state.get('total_cost', 0):.4f}")
    print(f"上次运行: {state.get('last_run', '从未')}")

    if pending:
        print(f"\n待处理文件（前 10 个）:")
        for f in pending[:10]:
            print(f"  - {f}")
        if len(pending) > 10:
            print(f"  ... 还有 {len(pending)-10} 个")

    price_mult = get_current_price_multiplier()
    if price_mult > 1.0:
        print(f"\n⚠ 当前为高峰时段 ({price_mult}x)，建议平价时段运行")


def main():
    parser = argparse.ArgumentParser(description="ASR 错误自动挖掘工具")
    sub = parser.add_subparsers(dest="mode")

    p_mine = sub.add_parser("mine", help="挖掘转录文件中的 ASR 错误")
    p_mine.add_argument("--limit", type=int, help="限制处理文件数（调试用）")
    p_mine.add_argument("--filter", type=str, help="只处理文件名包含该关键词的文件")

    p_merge = sub.add_parser("merge", help="审核候选并合并到术语表")

    p_status = sub.add_parser("status", help="查看挖掘进度")

    args = parser.parse_args()

    if args.mode == "mine":
        mine_mode(args)
    elif args.mode == "merge":
        merge_mode(args)
    elif args.mode == "status":
        status_mode(args)
    else:
        parser.print_help()
        print("\n示例:")
        print("  python mine_glossary.py status                    # 查看进度")
        print("  python mine_glossary.py mine --limit 5            # 试处理 5 个文件")
        print('  python mine_glossary.py mine --filter "独孤九剑"   # 只处理独孤九剑系列')
        print("  python mine_glossary.py mine                       # 处理所有待处理文件")
        print("  python mine_glossary.py merge                      # 审核候选并合并")


if __name__ == "__main__":
    main()
