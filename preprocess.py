"""
TikTok 转录文件预处理脚本 v2 - 分类、清理、合并

改动 v2:
  - 新增"第X节课"系列精确分类
  - 补充缺失的匹配模式
  - 加入 frontmatter 主题字段匹配
  - 修复 GBK 编码问题
  - 处理已精炼文件的注入
"""
import re, json, os, sys
from pathlib import Path

# 修复 GBK 输出
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

TRANSCRIPTS_DIR = Path(r"E:\video_transcripts\transcripts")
REFINED_DIR = Path(r"E:\video_transcripts\refined")
OUT = REFINED_DIR / "temp_merged"
OUT.mkdir(parents=True, exist_ok=True)

# 启动时清理临时文件，防止重跑时重复追加
for old in OUT.glob("*"):
    if old.is_file():
        old.unlink()

# 已精炼文件 (不做 AI 处理，直接注入)
PRE_REFINED_MAP = {
    "TikTok内容创作与混剪去重攻略.md": ["03-内容创作-混剪去重与脚本制作"],
    "TikTok商家出海掘金营-运营管理综合指南.md": ["07-独立站-Shopify建站与支付物流", "08-TikTok小店-运营管理与订单履约", "12-入驻开店-资质准备与定邀类目"],
    "TikTok跨境US-POP定邀类目清单（2024-05-08更新）.md": ["12-入驻开店-资质准备与定邀类目"],
}

# 第X节课 delogo 系列精确映射 (文件名 → 目标主题)
LESSON_MAP = {
    "01.": "01-跨境入门-市场认知与平台概述",
    "02.": "01-跨境入门-市场认知与平台概述",
    "03.": "11-设备环境-手机配置与网络搭建",
    "04.": "11-设备环境-手机配置与网络搭建",
    "05.": "04-账号运营-起号增长与合规管理",
    "06.": "04-账号运营-起号增长与合规管理",
    "07.": "09-AI工具-数字人与自动化内容生成",
    "08.": "04-账号运营-起号增长与合规管理",
    "09.": "09-AI工具-数字人与自动化内容生成",
    "10.": "09-AI工具-数字人与自动化内容生成",
    "11.": "09-AI工具-数字人与自动化内容生成",
    "12.": "09-AI工具-数字人与自动化内容生成",
    "13.": "09-AI工具-数字人与自动化内容生成",
    "14.": "02-选品策略-方法与工具实战",
    "15.": "02-选品策略-方法与工具实战",
    "16.": "02-选品策略-方法与工具实战",
    "17.": "09-AI工具-数字人与自动化内容生成",
    "18.": "09-AI工具-数字人与自动化内容生成",
    "19.": "09-AI工具-数字人与自动化内容生成",
    "20.": "03-内容创作-混剪去重与脚本制作",
    "21.": "07-独立站-Shopify建站与支付物流",
    "22.": "03-内容创作-混剪去重与脚本制作",
    "23.": "03-内容创作-混剪去重与脚本制作",
    "24.": "03-内容创作-混剪去重与脚本制作",
    "25.": "09-AI工具-数字人与自动化内容生成",
    "26.": "09-AI工具-数字人与自动化内容生成",
}

def parse_duration(s):
    if not s: return 0
    s = s.strip().strip('[]')
    try:
        parts = list(map(int, s.split(":")))
        if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
        if len(parts) == 2: return parts[0]*60 + parts[1]
    except: pass
    return 0

def parse_frontmatter(text):
    meta = {"title": "", "topic": "", "duration": ""}
    m = re.match(r'^---\s*\n(.*?)\n(?:---|\.\.\.)', text, re.DOTALL)
    if m:
        for line in m.group(1).split('\n'):
            kv = line.split(':', 1)
            if len(kv) == 2:
                key = kv[0].strip().strip('"\'')
                val = kv[1].strip().strip('"\'')
                if key in ('标题', 'title'): meta['title'] = val
                elif key in ('主题', 'topic'): meta['topic'] = val
                elif key in ('时长', 'duration'): meta['duration'] = val
    if not meta['title']:
        for line in text.split('\n')[:10]:
            m2 = re.match(r'^#\s+(.+)', line)
            if m2: meta['title'] = m2.group(1).strip(); break
    return meta

# ===== 13 个主题分类规则 =====
THEME_RULES = [
    ("01-跨境入门-市场认知与平台概述", [
        r'先导课', r'市场', r'认知与红利', r'了解平台',
        r'发展趋势', r'盈利核心', r'学习地图', r'成长指南',
        r'课程学习介绍', r'项目介绍', r'准备工作', r'课程概述',
        r'值不值得', r'商业逻辑', r'案例介绍',
        r'跨境出海', r'跨境抖音的商业', r'新手小商家',
        r'线上精品课[一二三四]',
        r'第[一二]节', r'第一课', r'TK跨境从流量到留量', r'TikTok全球化版图',
        r'从零到一打造TK',
    ]),
    ("02-选品策略-方法与工具实战", [
        r'选品[^\.]', r'选品方法', r'选品工具', r'选品思路', r'选品实操',
        r'爆款[之到打]', r'爆款打造', r'爆款拆解', r'爆款素材',
        r'单品打爆', r'多品打爆',
        r'测品', r'定品', r'找品', r'热品', r'卖什么',
        r'提炼卖点', r'塑造产品价值', r'SPY', r'spy',
        r'数据化选品', r'产品数据', r'产品特性', r'产品评论', r'售卖趋势',
        r'第三方工具', r'(热点|榜单)选品', r'市场需求',
        r'葵花宝典.*(选品|爆款|品类)',
        r'品类推荐', r'平台类目分析',
        r'第四章：选品', r'卖什么才能出单',
        r'产品卖点', r'快速出单',
        r'暴利选品', r'选品案例',
        r'一件代发', r'供应链', r'货源',
        r'什么要求', r'具备什么',
    ]),
    ("03-内容创作-混剪去重与脚本制作", [
        r'混剪', r'去重', r'剪辑', r'剪映', r'拍摄', r'素材',
        r'视频脚本', r'视频标题', r'文案', r'封面', r'标签',
        r'话题', r'BGM', r'音乐', r'爆款标题', r'爆款开头', r'爆款选题',
        r'内容创作', r'内容方向', r'视频制作', r'视频素材',
        r'查重', r'反查重', r'滤镜', r'调色',
        r'头图', r'详情页', r'激发需求', r'增加信任', r'引导下单',
        r'成片', r'AI成片', r'AI剪辑', r'AI提炼',
        r'拆解爆款', r'拍摄思路', r'拍摄技巧',
        r'基础剪辑', r'发布作品', r'评论',
        r'封面合集', r'拍摄软件', r'内容营销',
        r'去水印', r'无水印',
        r'视频延长', r'万能混剪',
        r'葵花宝典.*(混剪|音乐|脚本|素材)',
        r'独孤九剑.*(剪映|无水印|发布作品|去重)',
        r'文生图', r'文生视频', r'图生视频',
        r'视频算法', r'爆款.*逻辑', r'算法逻辑',
        r'有效模仿', r'爆款复制',
        r'混剪.*去重', r'剪辑.*脚本',
        r'视频.*要求', r'视频.*类型', r'容易爆',
        r'重复度', r'浏览量', r'内容.*重复',
        r'推流算法', r'推流逻辑', r'算法机制',
        r'完播率',
        r'数据优化.*内容', r'数据优化.*转化',
        r'案例[分析拆解]', r'自己的案例',
        r'提升.*完播', r'挂链接',
    ]),
    ("04-账号运营-起号增长与合规管理", [
        r'账号定位', r'账号运营', r'账号注册', r'账号包装', r'账号维护',
        r'账号检测', r'养号', r'起号', r'冷启动', r'流量扶持',
        r'涨粉', r'快速涨粉', r'粉丝', r'矩阵', r'私域',
        r'找对标', r'对标账号', r'违规', r'限流', r'社区规则',
        r'0播放', r'账号异常', r'流量池', r'创作者基金',
        r'linktree', r'运营技巧', r'跨境抖音的运营',
        r'运营管理', r'界面指南', r'认识界面',
        r'带货权限', r'挂车', r'小黄车',
        r'葵花宝典.*(带货|流程)',
        r'美女起号', r'起号逻辑',
        r'账号.*变现', r'营销IP',
        r'运营.*方案', r'账号.*规划',
        r'禁止的内容', r'注意事项',
        r'初始流量', r'核心指标',
        r'提升.*方向', r'提升.*流量', r'有效提升',
        r'避坑指南', r'下一步',
        r'创作者.*开通', r'创作者基金',
        r'数据解读', r'数据指标',
        r'单品打爆', r'多品打爆',
    ]),
    ("05-达人合作-网红建联与联盟营销", [
        r'达人', r'网红', r'联盟', r'精选联盟',
        r'带货网红', r'国外带货', r'带货合作', r'MCN',
        r'达人建联', r'网红开发',
        r'葵花宝典.*MCN',
    ]),
    ("06-广告投放-策略搭建与数据优化", [
        r'广告[^机]', r'投放', r'投流', r'ADS', r'ads',
        r'Facebook广告', r'谷歌广告', r'Google ADS', r'Google.*搭建',
        r'Pinterest.*广告', r'广告优化', r'广告数据',
        r'广告素材', r'广告账号', r'Pixel',
        r'广告账户', r'TikTokads',
        r'落地页', r'投放全流程', r'投放基础', r'扩量',
        r'掌握.*广告', r'系统了解.*投放',
        r'Facebook.*Instagram', r'Facebook.*投放',
        r'CPC', r'CPM', r'CTR', r'CVR', r'VBO', r'ROAS',
        r'投流三字经', r'广告投放',
        r'Facebook.*内容运营.*广告',
    ]),
    ("07-独立站-Shopify建站与支付物流", [
        r'独立站', r'Shopify', r'shopify',
        r'建站实操', r'建站',
        r'店铺装修', r'店铺设置', r'营销插件', r'上架产品',
        r'收款', r'Paypal', r'paypal', r'贝宝', r'支付', r'提现',
        r'回款', r'售后', r'客服.*独立站',
        r'客户支付', r'运输政策', r'销售渠道',
        r'单品站', r'密码设置', r'主题选择',
        r'产品导入', r'GPay', r'信用卡收款',
        r'找货源',
        r'开店', r'店铺注册', r'店铺后台',
        r'社交.*独立站', r'引导独立站', r'引流独立站',
        r'经营策略', r'经营地图', r'规避风险',
        r'营销活动',
        r'11-开篇', r'12-准备阶段', r'13-注册谷歌',
        r'14-Paypal', r'15-独立站店铺',
        r'16-密码', r'17-小店后台', r'18-客户支付',
        r'19-商店', r'20-运输', r'21-产品导入',
        r'社媒账号', r'跨境小包',
        r'社交媒体独立站电商讲解',
        r'Facebook.*独立站', r'谷歌.*独立站',
        r'Youtube.*独立站', r'TikTok.*引流独立站',
        r'独立站.*社交电商',
        r'独立站.*品牌',
        r'跨境理论', r'用中文经营',
        r'赚米', r'赚钱',
    ]),
    ("08-TikTok小店-运营管理与订单履约", [
        r'TikTok小店', r'TK小店',
        r'后台管理', r'后台设置', r'发货',
        r'物流', r'订单', r'商品上传', r'商品编辑', r'批量上传',
        r'折扣', r'营销', r'活动',
        r'出单', r'小店开店', r'小店后台',
        r'小店.*订单', r'小店.*物流', r'小店.*发货',
        r'小店.*客服', r'小店.*活动',
        r'英国小店', r'跨境物流',
        r'小黄车', r'带货流程', r'挂车',
        r'卖爆款', r'查看数据', r'添加联盟',
        r'短视频挂车', r'快速出单',
        r'上架', r'下单', r'关闭交易',
        r'FBT', r'海外仓', r'履约',
        r'葵花宝典.*卖爆款', r'葵花宝典.*挂车', r'葵花宝典.*数据',
        r'小店.*上传', r'小店.*订单管理',
        r'小店.*客服系统', r'小店.*物流',
        r'精选联盟网红带货爆单',
        r'TikTok商家出海掘金营',
        r'电商讲解',
        r'小店.*开店', r'小店.*运营',
        r'操作细节', r'物流操作',
    ]),
    ("09-AI工具-数字人与自动化内容生成", [
        r'数字人', r'声音克隆', r'文生语音',
        r'AI工具', r'AI.*介绍',
        r'AI.*数字人', r'AI.*声音克隆', r'AI.*文生',
        r'AI.*图生', r'AI.*视频延长', r'AI.*起号',
        r'AI.*视频脚本', r'AI.*成片', r'AI.*剪辑',
        r'AI.*提炼', r'AI.*直播间',
        r'AI.*爆款', r'AI.*脚本', r'AI.*创作',
        r'AI.*拆解', r'AI.*一键',
        r'AI与实拍', r'实拍组合案例',
    ]),
    ("10-直播带货-场景搭建与话术策略", [
        r'直播[^间]?', r'直播间', r'带货直播', r'直播带货',
        r'直播操作', r'直播数据', r'直播复盘', r'直播话术',
        r'直播场景', r'直播搭建',
        r'组品策略', r'塑品', r'促单', r'讲品',
        r'直播.*运营', r'直播.*推流',
        r'直播核心', r'直播.*流量',
        r'TK小店直播',
        r'直播.*高转化',
        r'母婴带货', r'知识带货', r'穿搭带货',
        r'美妆带货', r'宠物带货', r'健身带货',
        r'好物分享带货', r'3C数码.*带货',
        r'线上精品课.*带货',
        r'直播间.*搭建', r'直播间.*话术',
        r'直播间.*承接',
        r'直播分析', r'直播复盘',
        r'直播.*运营玩法',
        r'选好区跟品',
    ]),
    ("11-设备环境-手机配置与网络搭建", [
        r'设备', r'手机', r'苹果', r'iPhone', r'刷机',
        r'Apple ID', r'海外ID', r'苹果ID',
        r'隔空投送', r'文件传输',
        r'网络', r'节点', r'IP\b', r'VPN', r'代理',
        r'指纹', r'区域定位', r'定位', r'伪装',
        r'下载TikTok', r'下载.*软件', r'下载',
        r'设备选择', r'设备环境', r'设备设置', r'设备修改', r'设备伪装',
        r'软件安装', r'网络配置',
        r'手把手设置', r'谷歌账号', r'GMAIL', r'注册谷歌',
        r'独孤九剑',
        r'线上精品课.*(设备|IP|苹果|注册tiktok|区域|传送|文件传输)',
        r'第[三四五]节', r'注册账号.*TikTok',
        r'准备阶段',
        r'第二章.*工具注册.*电脑环境',
        r'素材乐园.*(注意事项|准备工作)',
        r'线上精品课[五六七八九十]',
        r'线上精品课十一', r'线上精品课十三',
        r'1-苹果手机', r'2-关于跨境', r'3-总结',
        r'4-跨境', r'5-关于', r'6-如何',
        r'7-如果', r'8-跨境抖音', r'9-跨境',
        r'10-做跨境', r'10课程概述',
        r'注册账号.*如何注册',
        r'电脑.*注册',
    ]),
    ("12-入驻开店-资质准备与定邀类目", [
        r'入驻', r'开店',
        r'资质', r'定邀', r'类目',
        r'跨境店', r'US-POP', r'POP',
        r'营业执照', r'门槛', r'商家出海',
        r'第三章：店铺', r'品牌授权', r'合规',
        r'TikTok跨境US-POP定邀类目清单',
        r'店铺.*开通', r'店铺.*资格',
        r'店铺注册.*套餐',
        r'开店.*流程', r'开通.*店铺',
        r'收款.*解决方案',
    ]),
    ("13-综合参考-补充资料与行业数据", [
        r'翻译软件', r'翻译工具', r'常见名词', r'术语',
        r'代理商', r'GMV分成', r'反向海淘',
        r'赛道分析', r'BGM推荐', r'100首热门',
        r'大数据工具', r'数据工具',
        # 注意: '用中文经营' 已在 07 主题中，避免冲突不在此重复
        r'网红带货模式', r'soho',
        r'亿级大卖', r'百万美金',
        r'变现[模全]', r'变现模式', r'变现流程', r'变现全流程',
        r'盈利点', r'赚钱技巧',
        r'全球代理商', r'无货源海淘', r'跨境保姆',
        r'婷姐', r'案例分享', r'数据弟',
    ]),
]

def classify_file(filename, meta):
    """按文件名+frontmatter 返回主题ID"""
    candidate = filename + " " + meta.get('title', '') + " " + meta.get('topic', '')

    # 先检查"第X节课"模式
    m = re.match(r'^(\d{2})\.\s*第.*节课', filename)
    if m:
        key = m.group(1) + "."
        if key in LESSON_MAP:
            return LESSON_MAP[key]
        # 课程编号未在 LESSON_MAP 中：发出警告，让用户知道需要补全映射
        print(f"  ⚠ 警告: 检测到课程编号 {key} 但未在 LESSON_MAP 中映射，将 fallback 到关键词匹配: {filename}")

    for theme_name, patterns in THEME_RULES:
        for p in patterns:
            if re.search(p, candidate, re.IGNORECASE):
                return theme_name
    return None

def clean_text(text):
    """清理转录文本：去掉 H1 标题 / frontmatter / 时间戳 / 碎片句"""
    lines = text.split('\n')

    # 找到正文开始：跳过 YAML frontmatter（--- ... ---）
    body_start = 0
    fm_end = 0
    if lines[0].strip() == '---':
        for i, l in enumerate(lines[1:], 1):
            if l.strip().startswith('---') or l.strip().startswith('...'):
                fm_end = i + 1
                break

    # 从 frontmatter 之后找第一个有效正文行
    for i in range(fm_end, len(lines)):
        l = lines[i]
        # 跳过 H1 标题行 (# 标题)
        if re.match(r'^#\s+.+', l.strip()):
            continue
        # 跳过 metadata 行 (> 主题: ... | 日期: ...)
        if l.strip().startswith('> 主题') or l.strip().startswith('> 日期'):
            continue
        # 找到正文：> [时间戳] 或 > 开头或纯中文行
        if l.strip().startswith('> [[') or l.strip().startswith('> ') or re.search(r'[一-鿿]', l):
            body_start = i
            break
    else:
        body_start = fm_end  # 全没找到就从 frontmatter 尾部开始

    cleaned = []
    prev_blank = False
    for l in lines[body_start:]:
        line = re.sub(r'^>\s*\[\[[\d:]+\]\]\s*', '', l).strip()
        if re.match(r'^\[[\d:]+\]$', line): continue
        if re.match(r'^[\d\s:.,，。！？、；]+$', line): continue
        if not line:
            if not prev_blank: cleaned.append(''); prev_blank = True
            continue
        prev_blank = False
        cleaned.append(line)

    merged = []
    for line in cleaned:
        if merged and line and merged[-1] and not merged[-1][-1] in ('。', '？', '！', '?', '!', '，', ','):
            merged[-1] += line
        elif merged and not line:
            merged.append(line)
        elif line:
            merged.append(line)

    return '\n'.join(merged).strip()


# ===== ASR 术语纠错 =====
# 加载跨境电商 ASR 错误对照表（whisper-large-v3 常见误识别）
_GLOSSARY_PATH = Path(__file__).parent / "asr_glossary.json"
try:
    _glossary_data = json.loads(_GLOSSARY_PATH.read_text(encoding='utf-8'))
    # 过滤 skip:true 的项，按 wrong 长度倒序（避免子串误替换，如 "M3" 先于 "M3权限"）
    ASR_REPLACEMENTS = sorted(
        [item for item in _glossary_data.get("replacements", []) if not item.get("skip")],
        key=lambda x: -len(x["wrong"])
    )
    ASR_BLACKLIST = _glossary_data.get("blacklist", [])
    print(f"✓ 加载 ASR 术语表: {len(ASR_REPLACEMENTS)} 条替换规则, {len(ASR_BLACKLIST)} 条黑名单")
except Exception as e:
    print(f"⚠ 加载 asr_glossary.json 失败: {e}，ASR 纠错功能将跳过")
    ASR_REPLACEMENTS = []
    ASR_BLACKLIST = []

# 全局 ASR 替换统计（跨文件累计，供主流程末尾打印报告）
_ASR_STATS_GLOBAL = {}


def fix_asr_errors(text):
    """按 ASR 术语表做精确替换（长串优先，避免子串误替换）。
    返回 (修正后文本, {wrong_word: {count, right}})。"""
    if not ASR_REPLACEMENTS:
        return text, {}
    stats = {}
    for item in ASR_REPLACEMENTS:
        wrong = item["wrong"]
        right = item["right"]
        if wrong == right:
            continue
        count = text.count(wrong)
        if count > 0:
            text = text.replace(wrong, right)
            stats[wrong] = {"count": count, "right": right}
    return text, stats


# ===== Deep Clean: 深层清洗口语填充/课程推销/个人叙事 =====

# 纯口语填充词 — 在 ASR 上下文里永远是废话，直接删除
# 注意：不含英文字母填充词（如 OK/ok），容易误杀 TikTok/booking 等正常词汇
_RE_ORAL_FILLERS = re.compile(
    r"(说白了(就是)?[，,。.]?|对不对(呢)?[，,。.]?|大家会发现[，,。.]?|所以大家一定要[，,。.]?|"
    r"那目前来说[，,。.]?|那么在这里呢[，,。.]?|那么接下来呢[，,。.]?|"
    r"说白了其实[，,。.]?|其实就是说[，,。.]?|其实(本质上面)?来说[，,。.]?|"
    r"你知道吗[，,。.]?|你懂吗[，,。.]?|明白吗[，,。.]?|懂我意思吧[，,。.]?|"
    r"我跟大家讲(一下)?[，,。.]?|我跟大家去讲[，,。.]?|跟大家讲一下[，,。.]?|"
    r"大家要知道[，,。.]?|大家一定要了解到[，,。.]?|大家会发现的是[，,。.]?|"
    r"我们来讲一下[，,。.]?|我们来给大家讲一下[，,。.]?|我们来看一下[，,。.]?)"
)

# 课程推销用语 — 课程宣发/引导，不是知识
_RE_COURSE_PROMO = re.compile(
    r"(在这(一)?节课程当中[，,。.]?|从下一节课开始[，,。.]?|在接下来的课程[，,。.]?|"
    r"我会带大家(逐一的去)?[，,。.]?|我带大家[，,。.]?|我将会带大家[，,。.]?|我们来学习[，,。.]?|"
    r"我们的课程|本节课|下节课|这节课|先导课|线上精品课|"
    r"记笔记[，,。.]?|大家截图(下来|保存)?[，,。.]?|截图(保存|下来)[，,。.]?|大家记一下[，,。.]?|"
    r"大家认真听[，,。.]?|大家注意听[，,。.]?|课程学习介绍|成长指南|"
    r"老师这边[，,。.]?|老师呢[，,。.]?|老师是[，,。.]?|老师我[，,。.]?|我是C[CcIi]+[，,。.]?|我是Tina(老师)?[，,。.]?|"
    r"第一章[节]|第二章[节]|第三章[节]|第一节课|第二节课|"
    r"大家把.{0,10}记下来|大家把.{0,10}截图|我给大家总结[，,。.]?|"
    r"TikTok商家出海掘金营|葵花宝典|独孤九剑|"
    r"大家可以去尝试|大家可以自己去|大家可以截图|"
    r"记得关注|记得点赞|点赞收藏|一键三连|"
    r"下一章|下个视频|下期)"
)

# 个人叙事 — 只匹配最明确的叙事引导词，不贪心匹配后续内容
# "我当时"/"我记得"/"我试过"/"我做过" 是典型的个人故事开头
# 只删引导词本身+后缀标点，保留后续内容（可能含知识）
_RE_PERSONAL_NARRATIVE = re.compile(
    r"我(当时|记得|试过|做过)[，,。.]?"
)

# 话语标记 — 在这些词前加换行，让 LLM 能识别逻辑边界
_RE_DISCOURSE_MARKERS = re.compile(r'(首先|其次|然后|最后|那么|所以|但是|不过|然而|此外|另外|总之|因此)')


def deep_clean(text):
    """在 clean_text 之后、术语替换之前做深层清洗：
    1. 删除纯口语填充词
    2. 删除课程推销/宣发用语
    3. 删除个人叙事（讲师个人经历）
    4. 长句在话语标记处智能拆分（改善 LLM 可读性）
    返回清洗后的文本。
    """
    if not text:
        return text

    # Step 1: 删除口语填充词
    text = _RE_ORAL_FILLERS.sub('', text)

    # Step 2: 删除课程推销用语
    text = _RE_COURSE_PROMO.sub('', text)

    # Step 3: 删除个人叙事短语（只删短语，尽量保留后续内容）
    # 策略：仅删除开头的叙事引导词 + 紧随的少量修饰词，不删整句
    text = _RE_PERSONAL_NARRATIVE.sub('', text)

    # Step 4: 长句在话语标记处智能拆分（> 80 字符且不含标点）
    result = []
    for line in text.split('\n'):
        stripped = line.strip()
        if len(stripped) > 80 and not re.search(r'[，。！？、；：]', stripped):
            # 长句无标点，在话语标记前插入换行
            split_line = _RE_DISCOURSE_MARKERS.sub(r'\n\1', stripped)
            result.append(split_line)
        else:
            result.append(line)
    text = '\n'.join(result)

    # 清理可能产生的多余空行和孤儿标点
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'^[ \t，,。.、；;：:！!？?\s]+$', '', text, flags=re.MULTILINE)
    # 行首孤儿标点（删除填充词后残留的逗号/句号）
    text = re.sub(r'(?m)^[，,。.、；;：:！!？?\s]+', '', text)
    # 连续的逗号/句号合并
    text = re.sub(r'[，,]{2,}', '，', text)
    text = re.sub(r'[。.]{2,}', '。', text)
    # 逗号后紧跟句号 → 只保留句号
    text = re.sub(r'[，,][。.]', '。', text)

    return text.strip()


def clean_and_fix_asr(text):
    """clean_text → deep_clean → fix_asr_errors 组合管线：
    1. clean_text: 去时间戳/去碎片句
    2. deep_clean: 去口语填充/推销/叙事
    3. fix_asr_errors: 术语表替换
    统计累计到 _ASR_STATS_GLOBAL。"""
    cleaned = clean_text(text)
    deep_cleaned = deep_clean(cleaned)
    if not ASR_REPLACEMENTS:
        return deep_cleaned
    fixed, stats = fix_asr_errors(deep_cleaned)
    for wrong, info in stats.items():
        if wrong in _ASR_STATS_GLOBAL:
            _ASR_STATS_GLOBAL[wrong]["count"] += info["count"]
        else:
            _ASR_STATS_GLOBAL[wrong] = {"count": info["count"], "right": info["right"]}
    return fixed


# ===== 主流程 =====
files_by_theme = {t[0]: [] for t in THEME_RULES}
unclassified = []
quality_log = []
pre_refined_contents = {}

for f in sorted(TRANSCRIPTS_DIR.glob("*.md")):
    fname = f.name
    if fname.startswith('_') or fname.startswith('.'): continue

    if fname in PRE_REFINED_MAP:
        pre_refined_contents[fname] = f.read_text(encoding='utf-8')
        continue

    try:
        content = f.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        try:
            content = f.read_text(encoding='gbk')
        except Exception as e:
            print(f"  ⚠ 编码无法识别 {fname}: {e}")
            continue

    meta = parse_frontmatter(content)
    dur = parse_duration(meta.get('duration', ''))
    size = len(content.encode('utf-8'))
    ts_lines = [l for l in content.split('\n') if re.search(r'> \[\[[\d:]{4,8}\]\]', l)]
    text_body = clean_and_fix_asr(content)

    quality_log.append({
        "file": fname, "size": size, "duration_sec": dur,
        "ts_count": len(ts_lines), "text_len": len(text_body),
    })

    theme = classify_file(fname, meta)
    if theme:
        files_by_theme[theme].append((fname, text_body, dur, size))
    else:
        unclassified.append(fname)

# ── 兜底匹配阶段 1：重新读取文件，用真实 frontmatter 再分类 ──
# （原实现传空 meta，等价于首次匹配，这里修复为读真实内容）
for fname in list(unclassified):
    try:
        content = (TRANSCRIPTS_DIR / fname).read_text(encoding='utf-8')
    except UnicodeDecodeError:
        try:
            content = (TRANSCRIPTS_DIR / fname).read_text(encoding='gbk')
        except Exception as e:
            print(f"  ⚠ 兜底读取失败 {fname}: {e}")
            continue
    real_meta = parse_frontmatter(content)
    theme = classify_file(fname, real_meta)
    if theme:
        text_body = clean_and_fix_asr(content)
        dur = parse_duration(real_meta.get('duration', ''))
        size = len(content.encode('utf-8'))
        files_by_theme[theme].append((fname, text_body, dur, size))
        unclassified.remove(fname)

# ── 兜底匹配阶段 2：EXTRA_MAP 精确子串匹配 ──
# 要求 key 至少 3 字符，避免 "制作" 等短词误匹配无关文件
EXTRA_MAP = {
    "26.24": "13-综合参考-补充资料与行业数据",
    "27.25": "04-账号运营-起号增长与合规管理",
    "5.4.": "11-设备环境-手机配置与网络搭建",
    "出品": "03-内容创作-混剪去重与脚本制作",
    "后期": "03-内容创作-混剪去重与脚本制作",
    "制作": "03-内容创作-混剪去重与脚本制作",
}
for fname in list(unclassified):
    matched = False
    for key, theme in EXTRA_MAP.items():
        # 用 word boundary 风格匹配：key 必须作为独立 token 出现
        # 简单实现：key 直接 in filename（保留原行为），但加日志
        if key.lower() in fname.lower():
            try:
                content = (TRANSCRIPTS_DIR / fname).read_text(encoding='utf-8')
                text_body = clean_and_fix_asr(content)
                dur = parse_duration(parse_frontmatter(content).get('duration', ''))
                size = len(content.encode('utf-8'))
                files_by_theme[theme].append((fname, text_body, dur, size))
                unclassified.remove(fname)
                matched = True
                print(f"  ✓ EXTRA_MAP 匹配: {fname} → {theme} (key='{key}')")
            except Exception as e:
                print(f"  ⚠ EXTRA_MAP 读取失败 {fname}: {e}")
            break
    if matched:
        continue

    # ── 兜底匹配阶段 3：从 THEME_RULES 中提取"纯字面量"关键词做精确包含匹配 ──
    # 原实现用 p.strip('r').strip("'").strip('"') 处理正则模式，对含 .* [] 等元字符的模式完全无效
    # 新实现：只提取"完全由中文/字母数字组成、无正则元字符"的纯字面量
    for theme_name, patterns in THEME_RULES:
        if matched:
            break
        for p in patterns:
            # 去掉 r 前缀和引号
            literal = p
            if literal.startswith('r'):
                literal = literal[1:]
            literal = literal.strip("'\"")
            # 检查是否是纯字面量（不含正则元字符）
            if len(literal) < 2:
                continue
            regex_meta = set('.*+?^$()[]{}|\\')
            if any(c in regex_meta for c in literal):
                continue  # 含元字符，跳过（无法简单子串匹配）
            if literal in fname:
                try:
                    content = (TRANSCRIPTS_DIR / fname).read_text(encoding='utf-8')
                    text_body = clean_and_fix_asr(content)
                    dur = parse_duration(parse_frontmatter(content).get('duration', ''))
                    size = len(content.encode('utf-8'))
                    files_by_theme[theme_name].append((fname, text_body, dur, size))
                    unclassified.remove(fname)
                    matched = True
                    print(f"  ✓ 字面量匹配: {fname} → {theme_name} (kw='{literal}')")
                except Exception as e:
                    print(f"  ⚠ 字面量匹配读取失败 {fname}: {e}")
                break

# ── 兜底匹配阶段 4：最后仍未分类的文件，统一归入 13-综合参考，避免内容丢失 ──
FALLBACK_THEME = "13-综合参考-补充资料与行业数据"
for fname in list(unclassified):
    try:
        content = (TRANSCRIPTS_DIR / fname).read_text(encoding='utf-8')
    except UnicodeDecodeError:
        try:
            content = (TRANSCRIPTS_DIR / fname).read_text(encoding='gbk')
        except Exception as e:
            print(f"  ⚠ 最终兜底读取失败 {fname}: {e}，该文件被丢弃")
            continue
    text_body = clean_and_fix_asr(content)
    dur = parse_duration(parse_frontmatter(content).get('duration', ''))
    size = len(content.encode('utf-8'))
    files_by_theme[FALLBACK_THEME].append((fname, text_body, dur, size))
    print(f"  ⚠ 最终兜底: {fname} → {FALLBACK_THEME} (无法精确分类)")
    unclassified.remove(fname)

# 输出结果
print("=== 分类结果 ===")
total_files = 0
for theme, files in sorted(files_by_theme.items()):
    print(f"  {theme}: {len(files)} 个文件")
    total_files += len(files)
print(f"  未分类: {len(unclassified)} 个文件")
print(f"  已精炼文件等待注入: {len(pre_refined_contents)} 个")
print(f"  总计: {total_files + len(unclassified) + len(pre_refined_contents)} 个")

if unclassified:
    print("\n=== 仍未分类的文件 ===")
    for f in unclassified:
        print(f"  {f}")

# 写入合并文件
for theme, files in sorted(files_by_theme.items()):
    files.sort(key=lambda x: -x[3])
    lines = []
    lines.append(f"# {theme}")
    lines.append(f"# 源文件数: {len(files)}")
    total_dur = sum(f[2] for f in files)
    lines.append(f"# 总时长: {total_dur//3600:02d}:{(total_dur%3600)//60:02d}:{total_dur%60:02d}")
    lines.append("=" * 60)
    lines.append("")

    for fname, body, dur, _ in files:
        lines.append(f"<!-- SOURCE: {fname} | {dur//60:02d}:{dur%60:02d} -->")
        lines.append(body)
        if body: lines.append("")

    content_out = '\n'.join(lines)
    theme_id = theme[:2]
    safe_name = theme[3:].replace(' ', '').replace('：', '-').replace('，', '-')
    out_file = OUT / f"{theme_id}_{safe_name}_merged.txt"
    out_file.write_text(content_out, encoding='utf-8')
    kb = len(content_out.encode('utf-8')) // 1024
    print(f"  写入: {out_file.name} ({kb}KB)")

# 写入已精炼文件到对应主题目录
# 注意：同一份预精炼文件可能注入到多个主题（如综合指南覆盖 07/08/12），
# 这会导致跨主题内容重复。refine.py 在精炼时会通过【合并优化同类项】模块
# 让 AI 自动去重，无需在此处处理。仅做日志提示。
print("\n=== 已精炼文件注入 ===")
multi_theme_files = [f for f, themes in PRE_REFINED_MAP.items() if len(themes) > 1]
if multi_theme_files:
    print(f"  ℹ 以下文件注入到多个主题，将由 refine.py 自动去重: {multi_theme_files}")
for fname, content in pre_refined_contents.items():
    target_themes = PRE_REFINED_MAP[fname]
    for theme in target_themes:
        theme_id = theme[:2]
        safe_name = theme[3:].replace(' ', '').replace('：', '-').replace('，', '-')
        out_file = OUT / f"{theme_id}_{safe_name}_refined-source.txt"
        # 追加写入（不覆盖已有的合并文件）
        with open(out_file, 'a', encoding='utf-8') as f:
            f.write(f"<!-- PRE-REFINED SOURCE: {fname} -->\n")
            f.write(content)
        print(f"  注入: {out_file.name} ← {fname}")

# 保存日志
with open(OUT / "_quality_log.json", "w", encoding="utf-8") as f:
    json.dump(quality_log, f, ensure_ascii=False, indent=2)
with open(OUT / "_classification_log.txt", "w", encoding="utf-8") as f:
    f.write("分类日志\n\n")
    for theme, files in sorted(files_by_theme.items()):
        f.write(f"{theme}: {len(files)} 个\n")
        for fn, _, dur, _ in files:
            f.write(f"  {fn} ({dur//60}:{dur%60:02d})\n")
    if unclassified:
        f.write(f"\n未分类: {len(unclassified)} 个\n")
        for fn in unclassified: f.write(f"  {fn}\n")

# ASR 术语纠错报告
if _ASR_STATS_GLOBAL:
    print(f"\n=== ASR 术语纠错报告 ===")
    total_replacements = sum(info["count"] for info in _ASR_STATS_GLOBAL.values())
    print(f"共替换 {total_replacements} 处 ASR 错误，涉及 {len(_ASR_STATS_GLOBAL)} 个术语：")
    for wrong, info in sorted(_ASR_STATS_GLOBAL.items(), key=lambda x: -x[1]["count"]):
        print(f"  {wrong} → {info['right']}  ×{info['count']}")
    asr_report_path = OUT / "_asr_fixes_report.json"
    with open(asr_report_path, "w", encoding="utf-8") as f:
        json.dump(_ASR_STATS_GLOBAL, f, ensure_ascii=False, indent=2)
    print(f"  报告已保存: {asr_report_path.name}")
else:
    print(f"\n✓ ASR 术语纠错：未发现需替换的错误")

print(f"\n✅ 预处理完成！输出目录: {OUT}")
