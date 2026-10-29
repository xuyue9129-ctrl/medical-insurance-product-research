"""规则抽取：从归档原文中找出各字段的候选句（草稿），供研究者确认后写入记录。

设计原则：
- 只“摘”不“编”：候选值都附带原文句子，句子取自抽取文本的连续片段，可被逐字核验；
- 高精度字段（公司全称、备案条款名、条款编号、投保年龄、保险期间、保证续保、等待期）可用 --apply-safe
  直接写入，状态为“规则抽取”（仍需人工确认）；其余字段只生成草稿，必须由研究者阅读后写入。
"""
from __future__ import annotations

import re
from collections import Counter

from .common import read_text
from .fetch import load_manifest
from .normalize import nfkc
from .rates import parse_rate_text, pick_age
from .records import load_record, save_record
from .textextract import text_to_pages
from .workspace import Workspace

DOC_PRIORITY = {"01": 0, "05": 1, "04": 2, "02": 3, "03": 4, "07": 5, "06": 6, "08": 8, "09": 9}
SAFE_FIELDS = ["insurer_full", "clause_names", "clause_codes", "entry_age", "term", "guaranteed_renewal", "waiting_period"]


def sentences(page: str) -> list[str]:
    """把一页文本切成句子：先拼接断行，再按句末标点切分；过长的句子按分号/逗号再切。"""
    t = re.sub(r"[ \t\u3000]+", " ", page)
    t = re.sub(r"\n+", "\n", t)
    joined = re.sub(r"(?<=[^\n。；;：:！？])\n(?=[^\n])", "", t)
    out = []
    for part in re.split(r"(?<=[。；;！？\n])", joined):
        s = part.strip()
        if not s:
            continue
        if len(s) > 220:
            out.extend(x.strip() for x in re.split(r"(?<=[，,、])", s) if x.strip())
        else:
            out.append(s)
    return out


def _clip(s: str, m: re.Match | None = None, width: int = 160) -> str:
    """截取句子中与命中位置相邻的连续片段（保持可核验）。"""
    s = s.strip()
    if len(s) <= width or m is None:
        return s[:width * 2]
    a = max(0, m.start() - width // 2)
    return s[a:a + width]


Rule = tuple  # (field, regex, value_builder, doc_types or None, keyword gate)


def _nows(x: str) -> str:
    return re.sub(r"\s+", "", x or "")


def _v_age(m: re.Match, s: str = "") -> str:
    g = m.groupdict()
    if g.get("days"):
        return f"出生满{g['days']}天至{g['hi']}周岁"
    born = re.search(r"出生(?:满|后)?\s*(\d{1,3})\s*[天日]", nfkc(s))
    if g.get("lo") in ("0", "00") and born:
        return f"出生满{born.group(1)}天至{g['hi']}周岁"
    return f"{g['lo']}至{g['hi']}周岁"


_TOC = re.compile(r"\d+\.\d+\s?[\u4e00-\u9fa5]")


def is_toc(s: str) -> bool:
    """目录/阅读指引行（“1.1 合同构成1.2 合同成立…”或带点状引导线）不作为候选。"""
    return len(_TOC.findall(s)) >= 3 or bool(re.search(r"[…．.]{6,}", s))


RULES: list[tuple] = [
    ("entry_age", re.compile(r"出生(?:满|后)?\s*(?P<days>\d{1,3})\s*[天日](?:（含）|\(含\))?\s*(?:至|-|—|～|~)\s*(?P<hi>\d{2,3})\s*周岁"), _v_age, None),
    ("entry_age", re.compile(r"(?<!\d)(?P<lo>\d{1,2})\s*周岁(?:（含）|\(含\))?\s*(?:至|-|—|～|~)\s*(?P<hi>\d{2,3})\s*周岁"), _v_age, None),
    ("term", re.compile(r"保险期间(?:为|是)?\s*(?P<n>\d{1,2}|一|三|五|十)\s*年"), lambda m: f"{m.group('n').replace('一', '1').replace('三', '3').replace('五', '5').replace('十', '10')}年", None),
    ("guaranteed_renewal", re.compile(r"保证续保(?:期间|期)?(?:为|是)?\s*(?P<n>\d{1,2})\s*年"), lambda m: f"保证续保{m.group('n')}年", None),
    ("guaranteed_renewal", re.compile(r"不保证续保"), lambda m: "不保证续保", None),
    ("renewal_rules", re.compile(r"(?:续保|重新投保|再次投保)[^。；]{0,40}?(?:至|到|不超过|最高)\s*(?P<n>\d{2,3})\s*周岁"), lambda m: f"续保/重新投保最高至{m.group('n')}周岁", None),
    ("renewal_rules", re.compile(r"(?:停售|停止销售)[^。；]{0,60}"), None, None),
    ("waiting_period", re.compile(r"(?P<n>\d{1,3})\s*[日天][^。；，]{0,14}?为等待期|等待期(?:为|是)\s*(?P<n2>\d{1,3})\s*[日天]"),
     lambda m: f"疾病等待期{m.group('n') or m.group('n2')}日", ("01", "05", "02")),
    ("waiting_period", re.compile(r"(?:意外|续保)[^。；]{0,30}无等待期"), None, None),
    ("deductible", re.compile(r"免赔额[^。；]{0,60}?(?P<amt>\d+(?:\.\d+)?\s*万?元)"), lambda m: "免赔额" + _nows(m.group("amt")), None),
    ("coinsurance", re.compile(r"(?:赔付|给付)比例[^。；]{0,60}?(?P<p>\d{1,3}\s*%)"), lambda m: f"赔付比例{m.group('p').replace(' ', '')}", None),
    ("no_social_insurance", re.compile(r"(?:未以|未经|未通过|未使用)[^。；]{0,30}(?:基本医疗保险|社会医疗保险|医保|社保|公费医疗)[^。；]{0,40}?(?P<p>\d{1,3}\s*%)"), lambda m: f"未经社保结算按{m.group('p').replace(' ', '')}", None),
    ("total_limit", re.compile(r"(?:年度|累计|总)?(?:给付)?限额[^。；]{0,30}?(?P<amt>\d+(?:\.\d+)?\s*万元)"), lambda m: "限额" + _nows(m.group("amt")), None),
    ("hospital_scope", re.compile(r"二级(?:及|或)?以上[^。；]{0,40}医院|(?:普通部|特需部|国际部|VIP部)[^。；]{0,30}|私立医院[^。；]{0,30}"), None, ("01", "05", "06")),
    ("advanced_therapy", re.compile(r"质子重离子|硼中子|光免疫|电场治疗|CAR-?T", re.I), None, ("01", "05", "06")),
    ("external_drugs", re.compile(r"(?:院外|外购)[^。；]{0,10}(?:药|器械)|特定药品|临床急需|特药"), None, ("01", "05", "06")),
    ("rehab", re.compile(r"康复(?:医疗|住院|治疗)?(?:保险金|费用)?"), None, ("01", "05")),
    ("outpatient", re.compile(r"门急诊(?:医疗)?(?:保险金|费用|责任)?"), None, ("01", "05")),
    ("special_outpatient", re.compile(r"特殊门诊|门诊手术|住院前后\s*\d+\s*[日天]"), None, ("01",)),
    ("critical_medical", re.compile(r"重大疾病(?:医疗)?保险金|重疾医疗"), None, ("01", "05")),
    ("preexisting_definition", re.compile(r"既往症(?:是)?指|既往症[:：]"), None, ("01", "04", "05")),
    ("preexisting_general", re.compile(r"既往症[^。；]{0,80}(?:不承担|不负|除外|赔付|给付|比例)"), None, ("01", "04", "05")),
    ("preexisting_serious", re.compile(r"(?:严重既往症|严重疾病|特定疾病)[^。；]{0,80}"), None, ("04", "05", "01")),
    ("first_year_rules", re.compile(r"首年|首个保险期间|第[一二三四五1-5]个?保险期间|连续(?:投保|续保)[^。；]{0,30}(?:第|满)"), None, ("01", "05")),
    ("claim_special", re.compile(r"共享免赔|共用免赔|免赔额[^。；]{0,20}递减|不可兼得|家庭(?:保单)?[^。；]{0,10}免赔"), None, ("01", "05")),
    ("underwriting_tools", re.compile(r"医保数据|医疗保障信息|健康优选|风险等级|体检|健康评估|核保"), None, ("02", "05", "03")),
    ("rate_factors", re.compile(r"费率因子|家庭保单|健康优选|风险等级|折扣|系数"), None, ("02", "05")),
    ("health_services", re.compile(r"健康管理服务|增值服务|直付|绿色通道|就医(?:服务|协助)"), None, ("01", "07", "05")),
    ("other_access", re.compile(r"累计居住|职业类别|职业|居住|社会医疗保险身份|家庭成员"), None, ("01", "05")),
    ("hd_disease_scope", re.compile(r"是否(?:患有|曾患有|被告知患有|曾被告知)[^。；]{0,80}"), None, ("03", "05", "08")),
    ("hd_history_items", re.compile(r"过去\s*\d+\s*(?:年|个月)[^。；]{0,60}(?:住院|手术|检查)|(?:住院|手术)[^。；]{0,20}累计[^。；]{0,20}\d+\s*[天日]"),
     None, ("03", "05", "08")),
    ("hd_exemptions", re.compile(r"仍可正常投保|可正常投保|不影响(?:正常)?投保|无需告知"), None, ("03", "05", "08")),
    ("hd_disclosure_effect", re.compile(r"未(?:履行)?如实告知[^。；]{0,80}|如实告知义务[^。；]{0,80}(?:解除|不承担|退还)"), None, ("01", "05")),
    ("channel", re.compile(r"(?:销售|投保)(?:渠道|平台)[^。；]{0,40}|互联网(?:保险)?业务"), None, ("05", "08")),
    ("launch_date", re.compile(r"(?P<y>20[1-3]\d)\s*年\s*(?P<m>\d{1,2})\s*月[^。；]{0,30}(?:上市|发布|推出|上线|开售)"), lambda m: f"{m.group('y')}-{int(m.group('m')):02d}", ("08", "05")),
]


def _insurer(pages: list[str]) -> list[tuple[str, str]]:
    pat = re.compile(r"[\u4e00-\u9fa5（）()]{2,30}?(?:保险股份有限公司|保险有限责任公司|保险有限公司|相互保险社)")
    hits = Counter()
    lines = {}
    for p in pages[:3]:
        for ln in p.splitlines():
            for m in pat.findall(re.sub(r"\s+", "", nfkc(ln))):
                m = re.sub(r"^(?:本合同由|由|我们指|指)", "", m)
                hits[m] += 1
                lines.setdefault(m, ln.strip())
    return [(k, lines[k]) for k, _ in hits.most_common(2)]


def extract_drafts(ws: Workspace, pid: str, max_per_field: int = 6) -> dict:
    """遍历产品全部归档文本，生成 {field: [候选]}。"""
    m = load_manifest(ws, pid)
    drafts: dict[str, list[dict]] = {}

    def add(field, value, quote, doc, page, rule):
        lst = drafts.setdefault(field, [])
        if any(d["quote"] == quote for d in lst):
            return
        lst.append({"value": value, "quote": quote, "doc": doc["file"], "doc_type": doc["doc_type"], "page": page, "rule": rule})

    docs = sorted([d for d in m["documents"] if d.get("text_file")], key=lambda d: DOC_PRIORITY.get(d["doc_type"], 9))
    for d in docs:
        pages = text_to_pages(read_text(ws.root / d["text_file"]))
        if d["doc_type"] in ("01", "02", "05"):
            for name, line in _insurer(pages):
                add("insurer_full", name, line, d, 1, "公司全称")
        if d["doc_type"] == "01":
            for cn in d.get("detected_clause_names") or []:
                add("clause_names", cn, cn + "条款", d, 1, "条款标题")
            for code in d.get("detected_clause_codes") or []:
                pg = next((i for i, p in enumerate(pages, 1) if re.sub(r"\s+", "", code) in re.sub(r"\s+", "", nfkc(p))), 1)
                add("clause_codes", code, code, d, pg, "条款编号")
        if d["doc_type"] == "02":
            for c in pick_age(parse_rate_text(pages), 50):
                ctx = " / ".join(c["context"])
                add("premium_50m", f"[块{c['block']}] {c['age']}: {' '.join(c['values'])}（上下文：{ctx}）", c["line"], d, c["page"], "费率表50岁行")
        for pno, page in enumerate(pages, 1):
            for s in sentences(page):
                if is_toc(s):
                    continue
                for field, rx, builder, types in RULES:
                    if types and d["doc_type"] not in types:
                        continue
                    if len(drafts.get(field, [])) >= max_per_field * 3:
                        continue
                    mm = rx.search(nfkc(s)) if field in ("entry_age", "term", "guaranteed_renewal", "waiting_period") else rx.search(s)
                    if not mm:
                        continue
                    quote = _clip(s, rx.search(s))
                    if not quote:
                        continue
                    value = (builder(mm, s) if builder is _v_age else builder(mm)) if builder else ""
                    add(field, value, quote, d, pno, rx.pattern[:24])
    for k in list(drafts):
        drafts[k].sort(key=lambda x: (DOC_PRIORITY.get(x["doc_type"], 9), x["page"]))
        drafts[k] = drafts[k][:max_per_field]
    return drafts


def _fill_identity(ws: Workspace, rec: dict, product: dict) -> list[dict]:
    """公司简称、销售名：原件中逐字出现则按原文核验写入，否则标“推定”并说明来源。"""
    from .evidence import load_pages
    from .normalize import match_key
    from .records import apply_item
    out = []
    F = rec["fields"]
    if F["product_name"]["status"] == "未填":
        name = product["product_name"]
        hit = None
        for d in sorted(load_manifest(ws, product["pid"])["documents"], key=lambda d: DOC_PRIORITY.get(d["doc_type"], 9)):
            if not d.get("text_file"):
                continue
            _, keys = load_pages(ws.root / d["text_file"])
            for i, k in enumerate(keys, 1):
                if match_key(name) in k:
                    hit = {"doc": d["file"], "page": i, "quote": name}
                    break
            if hit:
                break
        item = ({"field": "product_name", "value": name, "evidence": [hit], "by": "rule"} if hit else
                {"field": "product_name", "value": name, "status": "推定", "by": "rule",
                 "note": "销售名来自产品目录/登记信息，归档原件中未逐字出现（条款只写备案名）；以官方投保页为准"})
        out.append(apply_item(ws, rec, item))
    if F["company"]["status"] == "未填":
        ev = [dict(e) for e in rec["drafts"].get("insurer_full", [])[:1]]
        out.append(apply_item(ws, rec, {"field": "company", "value": product["company"], "status": "推定", "by": "rule",
                                        "note": "公司简称（行业通行叫法），承保主体全称见引文",
                                        "evidence": [{"doc": e["doc"], "page": e["page"], "quote": e["quote"]} for e in ev]}))
    return out


def run_extract(ws: Workspace, pid: str, apply_safe: bool = False) -> dict:
    from .catalog import get_entry, load_catalog
    from .records import apply_item
    product = ws.get(pid)
    rec = load_record(ws, product["pid"])
    rec["drafts"] = extract_drafts(ws, product["pid"])
    entry = get_entry(product.get("catalog_id") or "", load_catalog(ws.root)) if product.get("catalog_id") else None
    if entry and entry.get("overview"):
        rec["hints"] = {"source": f"样本摘要目录（{entry.get('overview_as_of', '')}，非原文，须用原件核验后才能写入）",
                        **entry["overview"]}
    applied = []
    applied += _fill_identity(ws, rec, product)
    if apply_safe:
        for f in SAFE_FIELDS:
            if rec["fields"].get(f, {}).get("status") not in ("未填", None):
                continue
            cands = [c for c in rec["drafts"].get(f, []) if c.get("value")]
            if not cands:
                continue
            if f == "clause_names":
                vals = []
                for c in cands:
                    if c["value"] not in vals:
                        vals.append(c["value"])
                item = {"field": f, "value": "；".join(vals), "status": "规则抽取", "by": "rule",
                        "evidence": [{"doc": c["doc"], "page": c["page"], "quote": c["quote"]} for c in cands[:4]]}
            elif f == "waiting_period":
                vals = Counter(c["value"] for c in cands)
                v = vals.most_common(1)[0][0]
                item = {"field": f, "value": v, "status": "规则抽取", "by": "rule",
                        "evidence": [{"doc": c["doc"], "page": c["page"], "quote": c["quote"]} for c in cands if c["value"] == v][:2]}
            else:
                c = cands[0]
                item = {"field": f, "value": c["value"], "status": "规则抽取", "by": "rule",
                        "evidence": [{"doc": c["doc"], "page": c["page"], "quote": c["quote"]}]}
            r = apply_item(ws, rec, item)
            applied.append(r)
    save_record(ws, rec)
    return {"pid": product["pid"], "draft_fields": {k: len(v) for k, v in rec["drafts"].items()}, "applied": applied,
            "hints": bool(rec.get("hints"))}
