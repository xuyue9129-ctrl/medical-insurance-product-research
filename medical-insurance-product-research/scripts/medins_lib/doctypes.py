"""资料类型识别与归档编号。

编号沿用既有研究归档习惯：
01 条款 | 02 费率表 | 03 健康告知 | 04 责任免除/既往症清单（可保不可赔） | 05 投保须知/产品说明书/告知书
06 医院/药品/机构清单 | 07 服务手册/理赔与服务指南 | 08 上市报道/宣传/测评（仅作线索） | 09 其他/截图
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

DOC_TYPES = {
    "01": "条款",
    "02": "费率表",
    "03": "健康告知",
    "04": "责任免除/既往症清单",
    "05": "投保须知/产品说明",
    "06": "医院/药品/机构清单",
    "07": "服务手册/理赔指南",
    "08": "上市报道/宣传/测评",
    "09": "其他",
}

# 核心资料：缺失时须在资料清单中明确标注
CORE_TYPES = ["01", "02", "03", "04", "05"]

# 规则按顺序匹配，先命中先生效（“责任免除”须先于“条款”，“客户告知书”须先于“健康告知”）
_NAME_RULES: list[tuple[str, re.Pattern]] = [
    ("05", re.compile(r"客户告知书|投保提示书|销售平台.*告知|投保人声明|被保险人声明|互联网保险业务信息披露")),
    ("03", re.compile(r"健康告知|健康问卷|健告|告知事项|健康声明|智能核保问卷")),
    ("02", re.compile(r"费率|保费表|价格表|基准保费|rate", re.I)),
    ("04", re.compile(r"责任免除(?:条款)?|免责(?:条款)?|严重既往症(?:清单)?|既往症(?:清单)?|严重疾病(?:清单)?|特定疾病清单|疾病清单|"
                      r"除外清单|五类|除外疾病|可保不可赔|可保即可赔|特别约定")),
    ("07", re.compile(r"服务手册|健康管理(?:服务)?|服务指南|理赔指南|理赔服务|直付服务|客户服务|服务协议|服务内容")),
    ("06", re.compile(r"医院清单|医院列表|医疗机构(?:清单|列表)?|药品目录|用药目录|药品清单|特药|药械|器械目录|质子|重离子|硼中子|光免疫|电场治疗|"
                      r"护理机构|直赔.*医院|直付.*(?:机构|医院)|医院名单|医院|药店|CAR-?T|清单|目录", re.I)),
    ("05", re.compile(r"投保须知|产品说明书|说明书|重要提示|投保规则|职业|产品介绍|保障计划|计划表|信息披露|常见问题|FAQ", re.I)),
    ("01", re.compile(r"条款|保险合同|合同条款|clause", re.I)),
    ("08", re.compile(r"上市|发布|新闻|报道|测评|宣传|海报|彩页|新品")),
]

_CONTENT_RULES: list[tuple[str, re.Pattern, int]] = [
    ("02", re.compile(r"费率表|基准费率|年交|投保年龄[（(]周岁[)）]"), 2),
    ("03", re.compile(r"健康告知|是否患有|是否曾经|过去\d+年内"), 2),
    ("05", re.compile(r"投保须知|产品说明书"), 1),
    ("04", re.compile(r"责任免除|严重既往症|特定疾病"), 1),
    ("06", re.compile(r"医院名称|医疗机构名称|药品名称|通用名"), 1),
    ("07", re.compile(r"服务手册|服务内容|服务流程"), 1),
    ("01", re.compile(r"保险责任|本合同|犹豫期|保险期间"), 3),
]


def filename_from_url(url: str) -> str:
    try:
        return unquote(urlparse(url).path.rsplit("/", 1)[-1])
    except Exception:
        return ""


_ACCESS_WORDS = re.compile(r"免健康告知|无健康告知|无需健康告知|免健告|无健告|免告知")


def classify_name(*texts: str) -> str | None:
    """按锚文本/文件名/标题识别资料类型，无法识别返回 None。

    资料类型词通常在标题末尾（“…医疗保险条款”“…费率表”），所以取“最靠后命中”的规则；
    并先去掉产品名里的“免健告/无健康告知”等准入字样，避免把“免健告版费率表”误判为健康告知。"""
    joined = _ACCESS_WORDS.sub("", " ".join(t for t in texts if t))
    best, best_end = None, -1
    for code, pat in _NAME_RULES:
        ends = [m.end() for m in pat.finditer(joined)]
        if ends and max(ends) > best_end:
            best, best_end = code, max(ends)
    return best


def classify_content(first_pages_text: str) -> str | None:
    """文件名不含信息（如 232701035_00033362.pdf）时，按前两页正文识别。"""
    head = (first_pages_text or "")[:4000]
    title_zone = head[:300]
    for code, pat in _NAME_RULES:  # 标题区域优先
        if code in ("08",):
            continue
        if pat.search(title_zone):
            return code
    best, best_hits = None, 0
    for code, pat, need in _CONTENT_RULES:
        hits = len(pat.findall(head))
        if hits >= need and hits > best_hits:
            best, best_hits = code, hits
    return best


def classify(anchor: str = "", url: str = "", title: str = "", first_pages_text: str = "") -> tuple[str, str]:
    """综合判断，返回 (编号, 判断依据)。"""
    code = classify_name(anchor, title)
    if code:
        return code, "锚文本/标题"
    code = classify_name(filename_from_url(url))
    if code:
        return code, "URL文件名"
    code = classify_content(first_pages_text)
    if code:
        return code, "正文关键词"
    return "09", "未识别"


def type_name(code: str) -> str:
    return DOC_TYPES.get(code, "其他")
