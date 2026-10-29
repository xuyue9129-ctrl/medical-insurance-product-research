"""从已核验的研究总表生成内置种子目录 assets/catalog/products.json。

用法：python tools/build_seed_catalog.py <研究总表.xlsx> [--as-of YYYY-MM-DD]

读取工作表：
- 产品总表：约 200 款医疗险的公司、名称、类型、准入通道、状态、证据等级、来源 URL
- Sheet1_产品总览：核心样本的 17 个对比字段（作为市场对照基线与“参考提示”，不直接当作核验值）
- 1012框架补充：健康体/次标体对照险种
公开版（--out，随仓库分发）只含产品名、公司、系列、官网链接；含研究摘要的完整目录只通过 --private-out 写到本机，不入库。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "medical-insurance-product-research" / "scripts"))

import openpyxl  # noqa: E402

from medins_lib.catalog import company_info, load_companies, make_pid, similarity  # noqa: E402
from medins_lib.common import write_json  # noqa: E402
from medins_lib.normalize import core_key, name_key, nfkc, years_in  # noqa: E402

BRANDS = ["蓝医保", "众民保", "长相安", "家安芯", "安医保", "e生保", "健康心享", "医药安欣", "臻爱无忧", "超越保",
          "欣享易生", "欣享人生", "太享保", "全医保", "暖医保", "康医保", "人人安康", "好医保", "尊享e生", "医药无忧",
          "泰享无忧", "福安康", "逸家", "康健华尊", "全民保", "惠民保", "e生安心", "安心", "微医保", "健康福", "百万任我行"]

PUBLIC_KEYS = ["id", "company", "insurer_full", "product_name", "series", "series_line", "aliases", "product_type",
               "clause_names", "version"]

OVERVIEW_COLS = ["序号", "公司名称", "产品名称/系列", "上市月份", "期间/续保", "健告类型", "结节、三高的处理", "销售渠道",
                 "既往症：特殊可赔／不赔", "等待期及特殊时间规则", "免赔额、赔付比例及特殊规则", "康复保障", "门急诊保障",
                 "外购药械、特药及急需药", "先进疗法", "50岁男性保费（元/年）", "保费口径与版本"]
OVERVIEW_KEYS = {"上市月份": "launch_date", "期间/续保": "term_renewal", "健告类型": "access_channel",
                 "结节、三高的处理": "nodules_three_highs", "销售渠道": "channel", "既往症：特殊可赔／不赔": "preexisting_special",
                 "等待期及特殊时间规则": "waiting_period", "免赔额、赔付比例及特殊规则": "deductible_coinsurance",
                 "康复保障": "rehab", "门急诊保障": "outpatient", "外购药械、特药及急需药": "external_drugs",
                 "先进疗法": "advanced_therapy", "50岁男性保费（元/年）": "premium_50m", "保费口径与版本": "premium_basis"}


def clean(v) -> str:
    return re.sub(r"\s+\n", "\n", str(v).strip()) if v not in (None, "") else ""


def series_of(name: str) -> str:
    n = nfkc(name)
    if "·" in name:
        head = name.split("·")[0].strip()
        if 1 < len(head) <= 12:
            return re.sub(r"^(平安|太保|人保)", "", head) or head
    for b in BRANDS:
        if b.lower() in n.lower():
            return b
    return ""


def line_of(name: str, ptype: str) -> str:
    s = name + ptype
    for k, v in [("中高端", "中高端"), ("中端", "中端"), ("长期", "长期医疗"), ("百万", "百万医疗"), ("住院", "住院医疗"),
                 ("门诊", "门诊"), ("特药", "特药"), ("慢病", "慢病专项")]:
        if k in s:
            return v
    return ptype or ""


def access_of(text: str) -> str:
    t = text or ""
    if "无健康告知" in t or "免健告" in t or t.strip() == "0":
        return "免健告"
    if "专项" in t or "带病" in t:
        return "专项带病体准入"
    if "易核" in t:
        return "易核"
    if "简易" in t or "宽松" in t:
        return "简易健告"
    if "标准" in t:
        return "标准健告"
    return t[:20]


def body_type_of(reason: str, access: str, role: str = "") -> str:
    r = (reason or "") + (role or "")
    if "健康体" in r:
        return "健康体"
    if "特定人群" in r or "慢病" in r or access == "专项带病体准入":
        return "专项带病体"
    if "惠民保" in r:
        return "普惠（惠民保）"
    if access in ("免健告", "简易健告", "易核"):
        return "次标体"
    return "待核"


def company_short(name: str, companies) -> tuple[str, str]:
    c = company_info(name, companies)
    if c:
        return c["short"], c["full"]
    return name, ""


def evidence_letter(s: str) -> str:
    m = re.match(r"\s*([ABCD])", s or "")
    return m.group(1) if m else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--as-of", default="2026-10-27")
    ap.add_argument("--out", default=str(ROOT / "medical-insurance-product-research" / "assets" / "catalog" / "products.json"),
                    help="公开版目录（随仓库分发）：只含产品名、公司、系列、官网链接")
    ap.add_argument("--private-out", help="另存含研究摘要的完整目录，如 ~/.medins-research/catalog.json（不入库）")
    ap.add_argument("--exclude", action="append", default=[], help="按名称片段排除的产品（可多次）")
    a = ap.parse_args()
    companies = load_companies()
    wb = openpyxl.load_workbook(a.xlsx, data_only=True)

    entries: dict[str, dict] = {}

    ws = wb["产品总表"]
    hdr = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(hdr) if h}
    for r in ws.iter_rows(min_row=2, values_only=True):
        name = clean(r[idx["产品名称"]])
        if not name:
            continue
        short, full = company_short(clean(r[idx["公司名称"]]), companies)
        ptype = clean(r[idx["产品类型"]])
        access = access_of(clean(r[idx["准入通道"]]))
        urls = re.findall(r"https?://[^\s;；，,）)]+", clean(r[idx["来源URL / 证据"]]))
        e = {
            "id": make_pid(short, name), "company": short, "insurer_full": full, "product_name": name,
            "series": series_of(name), "series_line": "", "aliases": [], "product_type": ptype,
            "access_channel": access, "body_type": body_type_of(clean(r[idx["原因"]]), access),
            "launch": "", "status": clean(r[idx["当前状态"]])[:40],
            "renewal_hint": clean(r[idx["保障期限/续保"]])[:60],
            "channel_hint": clean(r[idx["销售/核验渠道"]])[:60],
            "evidence_level": evidence_letter(clean(r[idx["证据等级"]])),
            "source_urls": urls[:6], "origin": f"seed:产品总表@{a.as_of}",
        }
        e["series_line"] = "·".join(x for x in (e["series"], line_of(name, ptype)) if x) if e["series"] else ""
        entries[e["id"]] = e

    used: set[str] = set()
    markers = re.compile(r"([A-H]款|H20\d\d[A-H]|活动版|后期版|庆典版|易享版|尊享版|优享版|易核|\d\.\d|\d号)")

    def attach(short: str, name: str, threshold: float = 0.80) -> dict:
        best, best_s = None, 0.0
        for e in entries.values():
            if e["id"] in used or name_key(e["company"]) != name_key(short):
                continue
            if set(markers.findall(nfkc(name))) != set(markers.findall(nfkc(e["product_name"]))):
                continue
            s = similarity(core_key(name), core_key(e["product_name"]))
            ey, ny = years_in(e["product_name"]), years_in(name)
            if ny and ey and not (ny & ey):
                s -= 0.3
            if s > best_s:
                best, best_s = e, s
        if best is not None and best_s >= threshold:
            used.add(best["id"])
            return best
        e = {"id": make_pid(short, name), "company": short, "insurer_full": company_short(short, companies)[1],
             "product_name": name, "series": series_of(name), "aliases": [], "origin": f"seed:Sheet1@{a.as_of}"}
        entries[e["id"]] = e
        used.add(e["id"])
        return e

    ws = wb["Sheet1_产品总览"]
    for r in ws.iter_rows(min_row=4, values_only=True):
        if not r[2]:
            continue
        row = dict(zip(OVERVIEW_COLS, [clean(x) for x in r]))
        short, _ = company_short(row["公司名称"], companies)
        name = row["产品名称/系列"].replace("\n", "")
        e = attach(short, name)
        if name_key(e["product_name"]) != name_key(name) and name not in e["aliases"]:
            e.setdefault("aliases", []).append(name)
        e["overview"] = {OVERVIEW_KEYS[k]: row[k] for k in OVERVIEW_KEYS if row.get(k)}
        e["overview_as_of"] = a.as_of
        e["research_role"] = "核心样本"
        e["body_type"] = "次标体"
        m = re.match(r"(\d{4})(?:-(\d{2}))?", row["上市月份"])
        if m:
            e["launch"] = m.group(0)
        e["access_channel"] = access_of(row["健告类型"]) or e.get("access_channel", "")
        e.setdefault("series", series_of(name))

    ws = wb["1012框架补充"]
    for r in ws.iter_rows(min_row=5, values_only=True):
        if not r[1] or not str(r[0] or "").isdigit() or "机制核验" in str(r[1]):
            continue
        role = clean(r[3])
        folder = clean(r[7])
        m = re.search(r"1012补充_(.+)$", folder.replace("\\", "/"))
        full_name = m.group(1) if m else clean(r[1])
        brand = clean(r[2])
        short = {"泰康": "泰康人寿", "平安人寿": "平安人寿", "新华": "新华人寿", "君龙": "君龙人寿", "国寿": "中国人寿",
                 "蓝医保": "太保健康", "平安健康": "平安健康"}.get(brand, brand)
        e = attach(short, full_name, threshold=0.92)
        if clean(r[1]) not in e["aliases"]:
            e["aliases"].append(clean(r[1]))
        e["research_role"] = role
        e["body_type"] = "健康体" if "健康体" in role else ("次标体" if "次标体" in role else e.get("body_type", "待核"))
        e["renewal_hint"] = clean(r[4])[:80]

    manual_aliases = {
        "蓝医保·免健告百万医疗（2027）": ["蓝医保免健告百万医疗2027", "蓝医保2027", "太保免健告百万2027"],
        "蓝医保·免健告长期医疗险（有福版）": ["蓝医保有福版", "有福版"],
        "蓝医保·百万医疗险（免健告版）": ["蓝医保免健告版", "蓝医保百万免健告", "蓝医保2025免健告"],
        "众民保·中高端医疗2026": ["众民保中高端2026", "众民保中高端26"],
        "众民保·百万医疗2026": ["众民保百万2026", "众民保2026"],
    }
    for e in entries.values():
        for k, al in manual_aliases.items():
            if name_key(e["product_name"]) == name_key(k) or name_key(k) in {name_key(x) for x in e.get("aliases", [])}:
                e["aliases"] = sorted(set(e.get("aliases", [])) | set(al))
        e["clause_names"] = e.get("clause_names", [])
        e.setdefault("launch", "")
        e.setdefault("status", "")
        if not e.get("series_line") and e.get("series"):
            e["series_line"] = "·".join(x for x in (e["series"], line_of(e["product_name"], e.get("product_type", ""))) if x)
        e["version"] = "/".join(sorted(years_in(e["product_name"]))) or ""

    # 合并同一产品的不同写法（如“众民保·中高端医疗2026”与“众民保·中高端医疗险2026”、“太保家安芯3.0”与“家安芯3.0”）
    prefixes = sorted({name_key(x) for c in companies for x in [c["short"], *c.get("aliases", [])] if len(name_key(x)) >= 2},
                      key=len, reverse=True)

    def dedupe_key(e: dict) -> str:
        k = core_key(e["product_name"]).replace("医疗", "").replace("险", "").replace("版", "")
        for p in prefixes:
            if k.startswith(p) and len(k) > len(p) + 2:
                k = k[len(p):]
                break
        return name_key(e["company"]) + "|" + k

    merged: dict[str, dict] = {}
    for e in entries.values():
        k = dedupe_key(e)
        if k not in merged:
            merged[k] = e
            continue
        keep, other = (merged[k], e) if (merged[k].get("overview") or not e.get("overview")) else (e, merged[k])
        keep["aliases"] = sorted(set(keep.get("aliases", [])) | set(other.get("aliases", [])) | {other["product_name"]})
        keep["source_urls"] = list(dict.fromkeys((keep.get("source_urls") or []) + (other.get("source_urls") or [])))[:8]
        for f in ("product_type", "access_channel", "status", "renewal_hint", "channel_hint", "evidence_level", "launch", "series_line"):
            if not keep.get(f) and other.get(f):
                keep[f] = other[f]
        merged[k] = keep
    excl = [x for x in a.exclude if x]
    full = sorted((e for e in merged.values()
                   if not any(x in e["product_name"] or any(x in al for al in e.get("aliases", [])) for x in excl)),
                  key=lambda x: (x["company"], x["product_name"]))
    removed = len(merged) - len(full)

    official = {d for c in companies for d in c.get("domains", [])}

    def public_url(u: str) -> bool:  # 只保留保险公司官网、无查询参数的链接（不含分享链接/渠道追踪参数）
        from urllib.parse import urlparse
        p = urlparse(u)
        host = (p.hostname or "").lower()
        return not p.query and any(host == d or host.endswith("." + d) for d in official)

    public = []
    for e in full:
        pe = {k: e[k] for k in PUBLIC_KEYS if e.get(k)}
        pe["source_urls"] = [u for u in e.get("source_urls", []) if public_url(u)][:4]
        pe["origin"] = "seed"
        public.append(pe)
    write_json(Path(a.out), public)
    print(f"公开版目录：{len(public)} 款 → {a.out}（已排除 {removed} 款：{'、'.join(excl)}；不含研究摘要与个人来源链接）")
    if a.private_out:
        write_json(Path(a.private_out).expanduser(), full)
        print(f"自有完整目录（含市场基线摘要 {sum(1 for e in full if e.get('overview'))} 款）→ {a.private_out}（本机使用，勿提交到仓库）")


if __name__ == "__main__":
    main()
