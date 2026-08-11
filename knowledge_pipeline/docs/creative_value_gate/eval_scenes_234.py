# -*- coding: utf-8 -*-
"""
Phase 0.5 Creative Value Gate — SCENES 2/3/4 A/B evaluation. ONE-OFF, read-only.
- Reads real 5-video Knowledge verbatim from E:\\food_ip_knowledge (no hand-written knowledge).
- A arm: owner task only. B arm: owner task + per-scene selected real Knowledge.
- Identical model / temperature / max_tokens / thinking-disabled / system prompt / output format.
- Writes nothing to the repo; outputs per-scene JSON to the temp dir.
"""
import json, os, sys, re, requests

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
TEMP = 0.3
MAX_TOKENS = 8000
OUT_DIR = r"C:\Users\HP\AppData\Local\Temp\claude\E--video-toolkit"
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
    "- risk_flags：这条视频要规避的风险或需要注意的坑\n\n"
    "【事实边界规则】\n"
    "1. 只能把 owner task 明确提供的经营事实当成已知事实。\n"
    "2. 老板没有提供的信息（包括但不限于：限量、即将卖完、排队、顾客反馈、销量很好、优惠、赠品、食材产地、具体品质证明、价格、客流情况）不得擅自当成确定事实。\n"
    "3. 如果这些信息可能提升创作：必须放入 missing_facts 并标记“需确认”，或写成“如果事实成立，可这样使用”的条件性建议。\n"
    "4. 不得为了增强内容张力而自行补齐经营事实（例如虚构新品、活动、限量、排队、顾客故事、优惠或赠品）。"
)

SCENES = [
    {
        "scene": 2,
        "owner_task": "今天店里没有新品、没有活动、没有特别事件，就是普通营业的一天。老板想拍一条视频，让附近顾客记住这家店，并愿意近期来吃。",
        "synth_ids": ["Q010", "Q011", "Q013"],
        "card_ids": [
            "KID_cc64eb57a60b",  # 用心经营是选题源头
            "KID_290622697584",  # 客流视频=记录真实善行
            "KID_4aed84e909fe",  # 真做是持续产出的根基
            "KID_36374ba42d4d",  # 从瞎拍到创作的朋友圈
            "KID_9763aabedc73",  # 持续客流内容公式
            "KID_e9ae2d8f2a61",  # 真做是IP内容的前提
            "KID_af9bbd7583e3",  # 服务用心是营销门店型内容
            "KID_1c9baa33e30a",  # 确定性爆点原则（压力项：无新品日是否用其编造）
        ],
        "rationale": "场景核心=普通营业日“什么值得拍”。命中 SRC0003 系列“记录你为别人好真做了什么/别人因为你好真做了什么”与“用心经营是选题源头”（Q010/Q011/Q013+卡 9/10/12/13/14/7），是本场景最相关真实产物；“服务用心是营销门店型内容”(#56) 提供普通营业日服务细节可拍的具体化；刻意加入“确定性爆点原则”(#30) 作压力测试：验证 B 是否会用“爆点”去编造新品/活动/限量，而非从真实经营中寻找确定性卖点。未注入其余营销门店型爆点系列卡片避免过载。",
    },
    {
        "scene": 3,
        "owner_task": "店里准备上一个 99 元双人套餐，老板想拍一条视频促进附近顾客到店，但不希望账号长期变成只靠低价促销吸引人的账号。",
        "synth_ids": ["Q004", "Q031", "Q020"],
        "card_ids": [
            "KID_a81ee8152922",  # 营销门店型内容定位（回答为什么选你的店）
            "KID_7b1329e45481",  # 营销门店型内容避谈价格（anti_pattern）
            "KID_001d7861fcb2",  # 套餐核心在价格
            "KID_40533b0cab50",  # 营销门店型内容可拍套餐
            "KID_088aea86ce2b",  # 少拍套餐，拍老板视角
            "KID_fddd06ac9852",  # 内容定调决定用户质量
            "KID_d2896ad76630",  # 营销门店型内容定位（经营目标决定内容方向）
            "KID_5cdd1ca48bb8",  # 低消费人群=薅羊毛人群
        ],
        "rationale": "场景核心=价格(真实信息)+即时转化 vs 账号长期定位的边界。Q004 条件明确“变现定位为门店引流→内容定位围绕顾客关心的菜品、环境、价格、服务等展开”，为“可提及价格”vs“低价=定位”提供依据；Q031 生意目标决定内容侧重；Q020 低决策成本→直接给到店理由，且高频复购支持长期。卡片注入两对冲突项：#42“套餐可拍” vs #43“少拍套餐”、#47“避谈价格” vs #44“套餐核心在价格”，测试 B 是否在适用条件内调和而非机械执行；#53“内容定调决定用户质量”与 #48“薅羊毛人群”直击“不想账号变纯促销号”的长期定位顾虑。未注入课程套餐案例卡片（CASE 11/12/14），避免案例错误迁移。",
    },
    {
        "scene": 4,
        "owner_task": "老板每天早上亲自去市场挑海鲜，他想拍一条视频。目标不是今天立刻卖多少，而是让附近顾客慢慢觉得这个老板懂海鲜、选货认真、值得信任。",
        "synth_ids": ["Q031", "Q020", "Q010"],
        "card_ids": [
            "KID_6f44ea065496",  # 人设IP讲故事与门店卖点
            "KID_cb12a3f2c9e3",  # 人设故事驱动复购
            "KID_6e36d9dbda74",  # IP吸引高质量用户
            "KID_cc64eb57a60b",  # 用心经营是选题源头
            "KID_290622697584",  # 客流视频=记录真实善行
            "KID_36374ba42d4d",  # 从瞎拍到创作的朋友圈
            "KID_cdafa6463b56",  # 新鲜是营销门店型核心（压力项：是否推即时转化）
        ],
        "rationale": "场景核心=长期信任型内容，老板本人即内容。Q031 明确“生意目标=建立信任”时的内容侧重；Q020 作对比压力（低客单行业默认“一条视频给出到店理由”），测试 B 是否机械套用到信任型目标；Q010 真实经营素材框架。卡片：#49/#54/#52 直击“老板本人是内容、靠人设信任吸引高质量用户、人设故事驱动复购”；#9/#10/#7 支撑“每天去市场挑海鲜”作为真实素材；#55“新鲜是营销门店型核心”作到店转化向压力项，测试 B 是否强行加入优惠/限量/CTA。",
    },
]


def load_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def build_knowledge_block(scene, synth, cards_by_id):
    lines = []
    lines.append("【可参考知识】以下来自这位老板学过的真实餐饮IP课程知识库（检索出的相关产物），仅作参考：适用的就用，不适用就忽略。")
    for i, qid in enumerate(scene["synth_ids"], 1):
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
    for i, kid in enumerate(scene["card_ids"], 1):
        c = cards_by_id.get(kid)
        if c is None:
            raise RuntimeError(f"card not found: {kid}")
        lines.append(
            f"\n----- 知识卡 {i} [{c.get('knowledge_type')}] {c.get('title')} -----\n"
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
    synth = {s["question_id"]: s for s in load_jsonl(os.path.join(KNOWLEDGE_DIR, "synthesis", "questions.jsonl"))}
    cards_by_id = {c["knowledge_id"]: c for c in load_jsonl(os.path.join(KNOWLEDGE_DIR, "atomic", "knowledge_cards.jsonl"))}

    combined = {}
    for sc in SCENES:
        kb = build_knowledge_block(sc, synth, cards_by_id)
        user_a = sc["owner_task"]
        user_b = sc["owner_task"] + "\n\n" + kb
        print(f"[scene {sc['scene']}] kb chars={len(kb)} | running A...", flush=True)
        raw_a = call(user_a)
        print(f"[scene {sc['scene']}] A done | running B...", flush=True)
        raw_b = call(user_b)
        ja, jb = parse_json(raw_a), parse_json(raw_b)
        report = {
            "scene": sc["scene"],
            "owner_task": sc["owner_task"],
            "selected_synth_ids": sc["synth_ids"],
            "selected_card_ids": sc["card_ids"],
            "rationale": sc["rationale"],
            "user_A": user_a,
            "user_B": user_b,
            "knowledge_block_chars": len(kb),
            "knowledge_block": kb,
            "arm_A_raw": raw_a,
            "arm_B_raw": raw_b,
            "arm_A_json": ja,
            "arm_B_json": jb,
        }
        combined[f"scene_{sc['scene']}"] = report
        with open(os.path.join(OUT_DIR, f"ab_scene{sc['scene']}.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"[scene {sc['scene']}] A parsed={'OK' if ja else 'FAIL'} B parsed={'OK' if jb else 'FAIL'} | saved ab_scene{sc['scene']}.json", flush=True)

    with open(os.path.join(OUT_DIR, "ab_scenes_234.json"), "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    print("ALL DONE -> ab_scenes_234.json", flush=True)


if __name__ == "__main__":
    main()
