"""扫描所有转录文件，用更合理的指标识别极低质量文件。

中文 whisper 转录特性：bytes/sec 天然偏低（中文单字 = 3 bytes，口语密度约 8-20 bytes/sec）
改用"有效转录段数 / 视频时长"作为核心指标。
"""
import os, re, json
from pathlib import Path
from food_ip_config import LEGACY_TRANSCRIPTS_DIR

SRC = LEGACY_TRANSCRIPTS_DIR

def parse_duration(s):
    s = s.strip().strip('[]')
    try:
        parts = list(map(int, s.split(":")))
        if len(parts) == 3:
            return parts[0]*3600 + parts[1]*60 + parts[2]
        elif len(parts) == 2:
            return parts[0]*60 + parts[1]
    except:
        pass
    return 0

def parse_frontmatter(text):
    m = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    meta = {}
    if m:
        for line in m.group(1).split('\n'):
            kv = line.split(':', 1)
            if len(kv) == 2:
                meta[kv[0].strip()] = kv[1].strip()
    return meta

results = {"good": [], "poor": [], "very_poor": [], "unknown": []}

for f in sorted(SRC.glob("*.md")):
    try:
        content = f.read_text(encoding='utf-8')
    except:
        continue

    size = len(content.encode('utf-8'))
    meta = parse_frontmatter(content)
    dur_str = meta.get("时长", meta.get("duration", "00:00"))
    dur = parse_duration(dur_str) if dur_str else 0

    # 统计转录段数（> [[MM:SS]] 格式行）
    ts_lines = [l for l in content.split('\n') if re.search(r'> \[\[[\d:]{4,8}\]\]', l)]
    ts_count = len(ts_lines)
    ts_per_min = ts_count / (dur/60) if dur > 0 else 0

    # 统计纯文本字符（排除 frontmatter、时间戳、空白）
    body_start = 0
    lines = content.split('\n')
    for i, l in enumerate(lines):
        if l.strip().startswith('> [['):
            body_start = i
            break
    text_chars = sum(len(re.sub(r'^> \[\[[\d:]{4,8}\]\]\s*', '', l).strip()) for l in lines[body_start:] if l.strip())

    # === 质量判定 ===
    # 规则1: 非转录文件（无 frontmatter 且无时间戳）→ unknown
    if dur == 0 and ts_count == 0:
        flag = "unknown"
    # 规则2: 极低质量：≥10分钟但转录段<8，或≥5分钟但转录段<3
    elif (dur >= 600 and ts_count < 8) or (dur >= 300 and ts_count < 3):
        flag = "very_poor"
    # 规则3: 转录段密度极低：<0.3 段/分钟 且总段数<15
    elif ts_per_min < 0.3 and ts_count < 15 and dur > 120:
        flag = "very_poor"
    # 规则4: 文本量极低：<500字但>5分钟
    elif text_chars < 500 and dur > 300:
        flag = "very_poor"
    # 规则5: 偏低质量：0.3-0.8 段/分钟
    elif ts_per_min < 0.8 and dur > 120:
        flag = "poor"
    # 规则6: 偏低质量：<1000字但>10分钟
    elif text_chars < 1000 and dur > 600:
        flag = "poor"
    else:
        flag = "good"

    entry = {
        "file": f.name,
        "size_bytes": size,
        "duration_sec": dur,
        "duration_min": round(dur/60, 1),
        "ts_count": ts_count,
        "ts_per_min": round(ts_per_min, 2),
        "text_chars": text_chars,
        "flag": flag
    }
    results[flag].append(entry)

# 输出报告
total = sum(len(v) for v in results.values())
print(f"=== 质量扫描报告 ===")
print(f"总文件数: {total}\n")
for flag in ["good", "poor", "very_poor", "unknown"]:
    files = results[flag]
    print(f"{flag}: {len(files)} 个")
    if len(files) <= 80:  # 只打印非 good 的详情
        for e in files:
            print(f"  [{e['ts_per_min']:>5.1f}段/分|{e['text_chars']:>5}字|{e['duration_min']:>5.1f}分] {e['file']}")

# 保存 JSON
with open(SRC / "_quality_scan2.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

# 待删除列表
print(f"\n=== 建议删除 (very_poor: {len(results['very_poor'])} 个) ===")
for e in results["very_poor"]:
    print(f"  [{e['ts_per_min']:>5.1f}段/分|{e['text_chars']:>5}字] {e['file']}")

# 如果过少/过多，输出手动调整提示
n = len(results["very_poor"])
if n < 30:
    print(f"\n⚠ 仅有 {n} 个极低质量文件，可能阈值过严。建议降低阈值或同时删除部分 poor 文件。")
elif n > 60:
    print(f"\n⚠ 有 {n} 个极低质量文件，可能阈值过宽。建议检查是否有误杀。")
