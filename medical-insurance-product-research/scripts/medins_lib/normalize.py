"""文本规范化：产品名比对键、原文引文比对键、中文数字与金额/比例解析。"""
from __future__ import annotations

import re
import unicodedata

_ZW = re.compile(r"[\u200b-\u200f\u2028-\u202f\u2060\ufeff\u00ad]")
_WS = re.compile(r"\s+")
_DASHES = str.maketrans({"—": "-", "–": "-", "－": "-", "‐": "-", "‑": "-", "―": "-", "~": "-", "～": "-",
                         "“": '"', "”": '"', "‘": "'", "’": "'", "〔": "[", "〕": "]", "【": "[", "】": "]",
                         "《": "<", "》": ">", "〈": "<", "〉": ">", "·": "", "•": "", "・": "", "∶": ":"})
_NAME_PUNCT = re.compile(r"[\s\-_/\\|,，.。、;；:：!！?？'\"()\[\]{}<>（）【】《》·•・+＋&＆*＊#＃@%％^…“”‘’]+")

CN_DIGITS = {"零": 0, "〇": 0, "○": 0, "Ｏ": 0, "一": 1, "壹": 1, "二": 2, "两": 2, "贰": 2, "三": 3, "叁": 3,
             "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陆": 6, "七": 7, "柒": 7, "八": 8, "捌": 8,
             "九": 9, "玖": 9}
CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
CN_BIG = {"万": 10_000, "萬": 10_000, "亿": 100_000_000}

GENERIC_WORDS = [
    "保险股份有限公司", "保险有限责任公司", "保险有限公司", "股份有限公司", "有限公司",
    "医疗保险", "医疗险", "保险产品", "保险", "产品", "计划", "互联网专属", "互联网",
]


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s or ""))


def match_key(s: str) -> str:
    """用于原文核验的比对键：全半角统一、去空白/零宽字符、统一破折号与引号、小写。

    PDF 抽取常在中英文之间插空格、在句中断行，比对时一律去掉空白。"""
    s = nfkc(s).translate(_DASHES)
    s = _ZW.sub("", s)
    s = _WS.sub("", s)
    s = s.replace("．", ".").replace("…", "...")
    return s.lower()


def cn_to_int(s: str) -> int | None:
    """把“三十”“一万二千”“二〇二六”这类中文数字转为整数；无法识别返回 None。"""
    s = s.strip()
    if not s:
        return None
    if all(ch in CN_DIGITS for ch in s):  # 逐位读法：二〇二六
        return int("".join(str(CN_DIGITS[ch]) for ch in s))
    total, section, num = 0, 0, 0
    seen = False
    for ch in s:
        if ch in CN_DIGITS:
            num = CN_DIGITS[ch]
            seen = True
        elif ch in CN_UNITS:
            section += (num or 1) * CN_UNITS[ch]
            num = 0
            seen = True
        elif ch in CN_BIG:
            section += num
            total += section * CN_BIG[ch]
            section, num = 0, 0
            seen = True
        else:
            return None
    return total + section + num if seen else None


_CN_RUN_BODY = r"(?:[零〇○一二两三四五六七八九壹贰叁肆伍陆柒捌玖十拾][零〇○一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟]*)"
_CN_NUM_RUN = re.compile(
    r"(?:(?<=百分之)|(?<=第))" + _CN_RUN_BODY +
    r"|(?<![\d.])" + _CN_RUN_BODY +
    r"(?=元|万元|千元|日|天|个月|年|周岁|岁|%|倍|次|项|种|类|条|家|号|级|期)")


def cn_numbers_to_arabic(s: str) -> str:
    """把带计量单位（或“百分之/第”引导）的中文数字串替换成阿拉伯数字，仅用于比对/解析。

    “百万医疗”“一般医疗”这类非计量用法不转换，避免误报。"""
    def rep(m: re.Match) -> str:
        v = cn_to_int(m.group(0))
        return str(v) if v is not None else m.group(0)
    return _CN_NUM_RUN.sub(rep, s)


def name_key(s: str) -> str:
    """产品名比对键：NFKC、小写、去标点空白，并统一常见版本写法。

    - “二〇二六版” → 2026版；“26版/26款” → 2026版；“三号” → 3号
    - 全角数字/字母 → 半角"""
    s = nfkc(s).lower().translate(_DASHES)
    s = _ZW.sub("", s)
    s = re.sub(r"[二][〇○零][一二三四五六七八九〇○零]{2}", lambda m: str(cn_to_int(m.group(0))), s)
    s = re.sub(r"(?<![\d.])([2-3]\d)(?=(?:年)?(?:版|款))", lambda m: "20" + m.group(1), s)
    s = re.sub(r"(?<=[\u4e00-\u9fff])(2\d)$", lambda m: "20" + m.group(1), s.strip())
    s = re.sub(r"([一二三四五六七八九十]+)(?=号)", lambda m: str(cn_to_int(m.group(1)) or m.group(1)), s)
    s = s.replace("年版", "版")
    s = _NAME_PUNCT.sub("", s)
    return s


def core_key(s: str) -> str:
    """去掉“医疗保险/保险/有限公司”等通用词后的核心名，用于模糊匹配。"""
    k = name_key(s)
    for w in sorted((name_key(x) for x in GENERIC_WORDS), key=len, reverse=True):
        if w and k != w:
            k = k.replace(w, "")
    return k or name_key(s)


def years_in(s: str) -> set[str]:
    return set(re.findall(r"(?<!\d)(20[1-3]\d)(?!\d)", name_key(s)))


def versions_in(s: str) -> set[str]:
    k = nfkc(s)
    out = set(re.findall(r"(?<![\d.])(\d{1,2}\.\d)(?![\d.])", k))
    out |= {f"{cn_to_int(x) or x}号" for x in re.findall(r"([0-9一二三四五六七八九十]+)号", k)}
    out |= {m.upper() + "款" for m in re.findall(r"(?<![A-Za-z])([A-Ha-h])款", k)}
    out |= {m.upper() + "款" for m in re.findall(r"[A-Za-z]20\d\d([A-Ha-h])(?![A-Za-z])", k)}
    return out


# ---------------- 数值解析（用于“取值—原文”一致性校验） ----------------
_NUM = r"(\d+(?:,\d{3})*(?:\.\d+)?)"
_MONEY = re.compile(_NUM + r"\s*(万|千|亿)?\s*(?:元|块)?")
_PERCENT = re.compile(_NUM + r"\s*%")
_PCT_CN = re.compile(r"百分之\s*" + _NUM)


def number_tokens(text: str) -> list[set[float]]:
    """抽取文本中的数值，每个数值给出等价形式集合（“1.5万元”→{1.5, 15000}）。"""
    t = cn_numbers_to_arabic(nfkc(text))
    tokens: list[set[float]] = []
    for m in _PCT_CN.finditer(t):
        tokens.append({float(m.group(1).replace(",", ""))})
    for m in _MONEY.finditer(t):
        raw = float(m.group(1).replace(",", ""))
        forms = {raw}
        unit = m.group(2)
        if unit:
            forms.add(raw * {"万": 10_000, "千": 1_000, "亿": 100_000_000}[unit])
        tokens.append(forms)
    return tokens


def numbers_in(text: str) -> set[float]:
    """文本中所有数值及其换算形式的并集。"""
    out: set[float] = set()
    for forms in number_tokens(text):
        out |= forms
    return out


def unmatched_numbers(value: str, quotes_text: str) -> list[str]:
    """返回取值中在原文里找不到的数字（忽略 0、1 这类序号/默认值与超长编号）。"""
    pool = numbers_in(quotes_text)
    missing = []
    for forms in number_tokens(value):
        if forms <= {0.0, 1.0} or min(forms) >= 1e9:
            continue
        if not (forms & pool):
            v = min(forms)
            missing.append(str(int(v)) if v == int(v) else str(v))
    return missing
