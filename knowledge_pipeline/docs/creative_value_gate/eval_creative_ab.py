# -*- coding: utf-8 -*-
"""
Phase 0.5 Creative Value A/B evaluation — ONE-OFF, read-only.
- Reads real 5-video Knowledge verbatim from E:\\food_ip_knowledge (no hand-written knowledge).
- A arm: owner task only. B arm: owner task + selected real Knowledge.
- Identical model / temperature / max_tokens / thinking-disabled / system prompt / output format.
- Writes nothing to the repo; outputs JSON to a temp file.
"""
import json, os, re, sys, io, requests

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
TEMP = 0.3
MAX_TOKENS = 8000
OUT = r"C:\Users\HP\AppData\Local\Temp\claude\E--video-toolkit\ab_output.json"

KNOWLEDGE_DIR = r"E:\food_ip_knowledge"

SYSTEM = (
    "你是餐饮IP短视频的内容导演。给定一位餐饮老板的真实经营场景与目标，"
    "请你做一次拍摄前的创作决策评估，判断这条视频该怎么拍。\n\n"
    "只输出一个 JSON 对象，不要输出任何其它文字、注释或代码围栏。"
    "JSON 必须包含以下 11 个字段（全部用中文；每个字段给简洁、具体、可执行的描述；不确定的写“需确认”）：\n"
    "- objective：这条视频要达成的生意目标\n"
    "- audience_value：目标观众是谁；这条视频给观众的核心价值/看下去的理由\n"
    "- core_material：视频的核心拍摄素材（具体到画面/事件/镜头内容）\n"
    "- angle：切入角度（这条视频用什么样的视角和说法来讲）\n"
    "- core_tension：核心张力/钩子（吸引人看下去的冲突或悬念）\n"
    "- proof：用来支撑核心卖点的证据/画面\n"
    "- information_flow：信息组织顺序（先讲什么、再讲什么、最后讲什么）\n"
    "- business_role：这条视频在门店生意中承担的角色（引流/复购/信任/品牌等）\n"
    "- performer_fit：出镜人是谁、是否与内容匹配、为什么\n"
    "- missing_facts：开拍前还需要向老板确认/补充的事实信息\n"
    "- risk_flags：这条视频要规避的风险或需要注意的坑"
)

OWNER_TASK = "今天店里刚到一批特别大的生蚝，老板想拍条视频，目标是吸引附近顾客到店。"


def load_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def build_knowledge_block():
    """Select ONLY high-relevance real items; verbatim from files."""
    synth = {s["question_id"]: s for s in load_jsonl(os.path.join(KNOWLEDGE_DIR, "synthesis", "questions.jsonl"))}
    cards = load_jsonl(os.path.join(KNOWLEDGE_DIR, "atomic", "knowledge_cards.jsonl"))
    by_title = {c.get("title"): c for c in cards}

    sel_synth_ids = ["Q004", "Q011", "Q020", "Q031", "Q032"]
    sel_card_titles = [
        "新鲜是营销门店型核心",          # principle: freshness = core 到店理由
        "营销门店型内容定位",            # principle: answer "why choose your store"
        "确定性爆点原则",                # principle: deterministic hook
        "营销内容只提概率不提保证",      # principle: trust language
        "开头钩子皆为技巧",              # principle: opening is designed
        "少拍套餐，拍老板视角",          # principle: avoid 套餐, boss perspective
        "营销门店型内容避谈价格",        # anti_pattern: don't lead with price
        "低客单行业转化逻辑",            # principle: one video -> 到店理由
    ]

    lines = []
    lines.append("【可参考知识】以下来自该老板学过的真实餐饮IP课程知识库（检索出的相关产物），仅作参考：适用的就用，不适用就忽略。")

    for i, qid in enumerate(sel_synth_ids, 1):
        s = synth[qid]
        conds = ""
        if s.get("conditions"):
            conds = "\n适用条件：" + "；".join(f"如果{c['条件']}→{c['影响']}" for c in s["conditions"])
        dl = ""
        if s.get("decision_logic"):
            dl = "\n决策步骤：" + "；".join(s["decision_logic"])
        lines.append(
            f"\n----- 综合答案 {i} [{qid}] -----\n"
            f"问题：{s['question']}\n"
            f"答案：{s['summary']}{conds}{dl}"
        )

    for i, title in enumerate(sel_card_titles, 1):
        c = by_title.get(title)
        if c is None:
            raise RuntimeError(f"card title not found: {title}")
        lines.append(
            f"\n----- 知识卡 {i} [{c.get('knowledge_type')}] {title} -----\n"
            f"核心观点：{c.get('core_idea')}"
        )
    return "\n".join(lines)


def parse_json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                pass
    return None


def call(user_prompt):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": TEMP,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "disabled"},
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(BASE_URL, json=payload, headers=headers, timeout=300)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
            print(f"[retry {attempt}] {last_err}")
        except Exception as e:
            last_err = str(e)
            print(f"[retry {attempt}] {last_err}")
    raise RuntimeError(f"LLM call failed: {last_err}")


def main():
    if not API_KEY:
        sys.exit("No DEEPSEEK_API_KEY")
    kb = build_knowledge_block()
    user_a = OWNER_TASK
    user_b = OWNER_TASK + "\n\n" + kb

    print(f"[B knowledge block] items: 5 syntheses + 8 cards | chars={len(kb)}")
    print("Running A (no knowledge)...")
    raw_a = call(user_a)
    print("Running B (with knowledge)...")
    raw_b = call(user_b)

    ja, jb = parse_json(raw_a), parse_json(raw_b)
    report = {
        "arm_A_raw": raw_a,
        "arm_B_raw": raw_b,
        "arm_A_json": ja,
        "arm_B_json": jb,
        "knowledge_block_chars": len(kb),
        "knowledge_block_head": kb[:200],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[A parsed] {'OK' if ja else 'FAIL'} fields={list(ja) if ja else []}")
    print(f"[B parsed] {'OK' if jb else 'FAIL'} fields={list(jb) if jb else []}")
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    main()
