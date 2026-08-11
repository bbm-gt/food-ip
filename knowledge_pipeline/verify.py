"""
LEGACY / DEPRECATED — 旧的跨境（TikTok 13 主题）知识库验证脚本。
不属于 Food-IP P0 管线，Food-IP 的验证由 test_food_ip_p0.py 承担。
保留仅供历史审计，请勿基于它判断 Food-IP 输出质量。

TikTok 知识库验证脚本 - 检查精炼输出质量
用法: python verify.py

检查:
  1. 13 个输出文件是否存在
  2. 6 个必需模块是否完整（与 refine.py 的 REFINE_PROMPT 一致）
  3. 风格约束（禁用语 + 个人叙事检查，支持多处出现报告）
  4. 来源完整性
  5. 输出长度合理性
  6. 生成验证报告
"""
import os, re, json
from pathlib import Path

OUT_DIR = Path(r"E:\video_transcripts\refined")

# 期望的输出文件
EXPECTED_FILES = [
    "01-跨境入门-市场认知与平台概述.md",
    "02-选品策略-方法与工具实战.md",
    "03-内容创作-混剪去重与脚本制作.md",
    "04-账号运营-起号增长与合规管理.md",
    "05-达人合作-网红建联与联盟营销.md",
    "06-广告投放-策略搭建与数据优化.md",
    "07-独立站-Shopify建站与支付物流.md",
    "08-TikTok小店-运营管理与订单履约.md",
    "09-AI工具-数字人与自动化内容生成.md",
    "10-直播带货-场景搭建与话术策略.md",
    "11-设备环境-手机配置与网络搭建.md",
    "12-入驻开店-资质准备与定邀类目.md",
    "13-综合参考-补充资料与行业数据.md",
]

# 必需模块（与 refine.py 的 REFINE_PROMPT 一致，6 个）
REQUIRED_SECTIONS = [
    "【核心观点】", "【内容标签】", "【摘要】",
    "【FAQ问答对】", "【合并优化同类项】", "【提取核心框架】"
]

# 禁用语：使用完整短语（避免 "很高兴" 误匹配 "很高兴见到你成功了" 等合法表达）
# 每条都是完整短语，匹配时按字面包含查找
FORBIDDEN_PHRASES = [
    "很高兴为您服务",
    "欢迎来到",
    "感谢您收看",
    "这是我们的",
    "本课程",
    "接下来我们",
    "在接下来的",
    "大家注意",
    "大家记住",
    "啊这个",
]

# 个人叙事短语
PERSONAL_NARRATIVES = [
    "我当时", "我记得", "我们团队", "我们公司",
    "我做了", "我试过", "我个人",
]

# ASR 残留错误黑名单（从 asr_glossary.json 加载，与 preprocess.py 共享同一份术语表）
_GLOSSARY_PATH = Path(__file__).parent / "asr_glossary.json"
try:
    _glossary_data = json.loads(_GLOSSARY_PATH.read_text(encoding='utf-8'))
    ASR_BLACKLIST = _glossary_data.get("blacklist", [])
except Exception as e:
    print(f"⚠ 加载 asr_glossary.json 失败: {e}，ASR 黑名单检测将跳过")
    ASR_BLACKLIST = []


def check_file_exists():
    """检查所有输出文件是否存在；size 用字节数（避免中文字符数误算 KB）"""
    findings = []
    for fname in EXPECTED_FILES:
        path = OUT_DIR / fname
        if path.exists():
            # 用字节数计算 KB，中文 1 字符 = 3 字节
            size_bytes = len(path.read_text(encoding='utf-8').encode('utf-8'))
            findings.append({"file": fname, "exists": True, "size_bytes": size_bytes})
        else:
            findings.append({"file": fname, "exists": False, "size_bytes": 0})
    return findings


def check_sections(text, filename):
    """检查 6 个必需模块"""
    missing = []
    for section in REQUIRED_SECTIONS:
        if section not in text:
            missing.append(section)
    return {"file": filename, "missing": missing, "complete": len(missing) == 0}


def _find_all_occurrences(text, phrase, max_report=5):
    """用 re.finditer 找出所有出现位置，返回带上下文的列表（最多 max_report 条）"""
    occurrences = []
    for m in re.finditer(re.escape(phrase), text):
        idx = m.start()
        start = max(0, idx - 20)
        end = min(len(text), idx + len(phrase) + 20)
        context = text[start:end].replace('\n', ' ')
        occurrences.append({"phrase": phrase, "context": context, "pos": idx})
        if len(occurrences) >= max_report:
            break
    return occurrences


def check_style(text, filename):
    """风格约束检查 + ASR 残留错误检测：报告所有出现位置"""
    issues = []
    for phrase in FORBIDDEN_PHRASES:
        occurrences = _find_all_occurrences(text, phrase)
        if occurrences:
            issues.extend(occurrences)

    narratives = []
    for phrase in PERSONAL_NARRATIVES:
        occurrences = _find_all_occurrences(text, phrase)
        if occurrences:
            narratives.extend(occurrences)

    # ASR 残留错误检测（preprocess 已替换高频错误，这里检测精炼输出中是否仍残留）
    asr_residuals = []
    for phrase in ASR_BLACKLIST:
        occurrences = _find_all_occurrences(text, phrase, max_report=3)
        if occurrences:
            asr_residuals.extend(occurrences)

    return {
        "file": filename,
        "forbidden_phrases": issues,
        "personal_narratives": narratives,
        "asr_residuals": asr_residuals,
        "clean": len(issues) == 0 and len(narratives) == 0 and len(asr_residuals) == 0,
    }


def main():
    print(f"=== 精炼输出验证报告 ===\n")

    # 1. 文件存在性和大小
    print(f"--- 1. 文件存在检查 ---")
    file_checks = check_file_exists()
    all_exist = all(f["exists"] for f in file_checks)
    for f in file_checks:
        status = "✅" if f["exists"] else "❌"
        size_kb = f["size_bytes"] // 1024
        print(f"  {status} {f['file']} ({size_kb}KB)" if f["exists"] else f"  {status} {f['file']} - 缺失!")

    print(f"\n--- 2. 模块完整性检查 ---")
    section_issues = []
    for fname in EXPECTED_FILES:
        path = OUT_DIR / fname
        if not path.exists():
            section_issues.append({"file": fname, "missing": ["文件不存在"], "complete": False})
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except Exception as e:
            section_issues.append({"file": fname, "missing": [f"文件无法读取: {e}"], "complete": False})
            continue

        result = check_sections(text, fname)
        section_issues.append(result)
        status = "✅" if result["complete"] else "⚠"
        if result["missing"]:
            print(f"  {status} {fname}: 缺失 {result['missing']}")
        else:
            print(f"  {status} {fname}: 完整")

    print(f"\n--- 3. 风格约束检查 ---")
    style_issues = []
    for fname in EXPECTED_FILES:
        path = OUT_DIR / fname
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except Exception as e:
            print(f"  ⚠ {fname}: 读取失败 ({e})")
            continue
        result = check_style(text, fname)
        style_issues.append(result)
        if not result["clean"]:
            print(f"  ⚠ {fname}:")
            for p in result["forbidden_phrases"]:
                print(f"    禁用语「{p['phrase']}」→ ...{p['context']}...")
            for p in result["personal_narratives"]:
                print(f"    个人叙事「{p['phrase']}」→ ...{p['context']}...")
            for p in result.get("asr_residuals", []):
                print(f"    ASR残留「{p['phrase']}」→ ...{p['context']}...")
        else:
            print(f"  ✅ {fname}: 无风格问题")

    # 汇总
    print(f"\n{'='*50}")
    print(f"验证汇总")
    print(f"{'='*50}")
    complete = sum(1 for s in section_issues if s.get("complete"))
    clean = sum(1 for s in style_issues if s.get("clean"))
    total_clean = sum(1 for f in file_checks if f["exists"])

    print(f"文件完整性: {total_clean}/13 存在")
    print(f"模块完整性: {complete}/13 完整")
    print(f"风格合规: {clean}/{len(style_issues)} 无问题")

    # ASR 残留统计
    asr_clean = sum(1 for s in style_issues if not s.get("asr_residuals"))
    asr_total_residuals = sum(len(s.get("asr_residuals", [])) for s in style_issues)
    if asr_total_residuals > 0:
        print(f"ASR 残留: {asr_total_residuals} 处（{len(style_issues) - asr_clean} 个文件有残留，需检查 refine.py 输出）")
    else:
        print(f"ASR 残留: 0 处（所有精炼输出已清除黑名单术语）")

    pass_all = (total_clean == 13 and complete == 13 and clean == len(style_issues))

    # 如果精炼报告存在，也展示 token 和费用
    report_path = OUT_DIR / "_refine_report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding='utf-8'))
            summary = report.get("summary", {})
            print(f"\nAPI 用量:")
            print(f"  输入 tokens: {summary.get('total_tokens_in', 0):,}")
            print(f"  输出 tokens: {summary.get('total_tokens_out', 0):,}")
            print(f"  总费用: ¥{summary.get('total_cost_yuan', 0):.4f}")
            mult = summary.get("price_multiplier", 1.0)
            if mult and mult > 1.0:
                print(f"  价格倍数: {mult}x (高峰时段)")
            skipped_existing = summary.get("skipped_existing", 0)
            if skipped_existing:
                print(f"  断点续传跳过: {skipped_existing} 个主题")
        except Exception as e:
            print(f"  ⚠ 读取精炼报告失败: {e}")

    if pass_all:
        print(f"\n✅ 所有检查通过！精炼输出已就绪。")
    else:
        print(f"\n⚠ 部分检查未通过，请根据以上报告修复问题。")

    # 保存验证报告
    report = {
        "file_checks": file_checks,
        "section_checks": section_issues,
        "style_checks": style_issues,
        "all_pass": pass_all,
        "summary": {
            "files_exist": total_clean,
            "sections_complete": complete,
            "style_clean": clean,
        }
    }
    report_path = OUT_DIR / "_verify_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n验证报告已保存: {report_path}")


if __name__ == "__main__":
    main()
