"""产品横向对比与新旧版本对比。

方向判断（放宽/收紧/增强/涨价…）只对能数值化的字段给“候选判断”，必须由研究者结合条款复核：
例如健告题数减少不等于准入放宽（要看删掉的问项原本是否拒保），免赔额结构重排可能对不同人群方向相反。
"""
from __future__ import annotations

import re

from .common import now_iso, write_json
from .normalize import nfkc, numbers_in
from .records import field_map, load_record, schema_fields
from .workspace import Workspace

_ITEM_SPLIT = re.compile(r"[；;。\n]|(?<=[^\d])、")


def _items(s: str) -> list[str]:
    return [x.strip(" ，,：:") for x in _ITEM_SPLIT.split(nfkc(s or "")) if len(x.strip(" ，,：:")) >= 2]


def _first(nums: set[float], prefer_big: bool = False) -> float | None:
    vals = sorted(v for v in nums if v not in (0.0, 1.0)) if nums else []
    if not vals:
        return 0.0 if nums and 0.0 in nums else None
    return vals[-1] if prefer_big else vals[0]


def _money(s: str) -> float | None:
    """取主责任的免赔额：先找“万/元”金额（最小值），没有金额且写明0免赔时为0。"""
    t = nfkc(s)
    vals = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(万)?\s*元?", t):
        v = float(m.group(1)) * (10000 if m.group(2) else 1)
        if v >= 100:
            vals.append(v)
    if vals:
        return min(vals)
    if re.search(r"(?<!\d)0\s*免赔|免赔额?\s*0(?!\.\d)|零免赔", t):
        return 0.0
    return None


def _percent(s: str) -> float | None:
    vals = [float(x) for x in re.findall(r"(\d{1,3}(?:\.\d+)?)\s*%", nfkc(s))]
    return max(vals) if vals else None


def _days(s: str) -> float | None:
    vals = [float(x) for x in re.findall(r"(\d{1,3})\s*[日天]", nfkc(s))]
    return min(vals) if vals else None


def _years_renew(s: str) -> float | None:
    t = nfkc(s)
    if "不保证续保" in t:
        return 0.0
    vals = [float(g) for pair in re.findall(r"保证续保\s*(\d{1,2})\s*年|保续\s*(\d{1,2})\s*年", t) for g in pair if g]
    if "终身" in t:
        return 99.0
    return max(vals) if vals else None


def _age_max(s: str) -> float | None:
    vals = [float(x) for x in re.findall(r"(\d{2,3})\s*周岁", nfkc(s))]
    return max(vals) if vals else None


def _premium(s: str) -> float | None:
    vals = [float(x.replace(",", "")) for x in re.findall(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d{2,6})(?![\d.]*%)", nfkc(s))]
    vals = [v for v in vals if 20 <= v <= 200000 and not (2000 <= v <= 2100 and len(vals) > 1)]
    return vals[0] if vals else None


def _count(s: str) -> float | None:
    if "免健告" in s or "无健康告知" in s:
        return 0.0
    m = re.search(r"(\d{1,2})", nfkc(s))
    return float(m.group(1)) if m else None


DIRECTION_RULES = {
    "deductible": (_money, "免赔额提高（收紧）", "免赔额降低（放宽）"),
    "coinsurance": (_percent, "赔付比例提高（放宽）", "赔付比例降低（收紧）"),
    "waiting_period": (_days, "等待期延长（收紧）", "等待期缩短（放宽）"),
    "guaranteed_renewal": (_years_renew, "保证续保期延长（增强）", "保证续保期缩短（减弱）"),
    "entry_age": (_age_max, "投保年龄上限提高（扩大）", "投保年龄上限降低（收窄）"),
    "total_limit": (lambda s: _first(numbers_in(s), True), "总限额提高（增强）", "总限额降低（减弱）"),
    "premium_50m": (_premium, "50岁保费上涨", "50岁保费下降"),
    "hd_count": (_count, "健告问项增加（需看新增问项是否拒保）", "健告问项减少（不等于准入放宽，需看删除问项原本是否拒保）"),
    "no_social_insurance": (_percent, "未经社保结算比例提高（放宽）", "未经社保结算比例降低（收紧）"),
}


def _val(rec: dict, key: str) -> str:
    f = rec["fields"].get(key, {})
    if f.get("status") in ("未填",):
        return ""
    if f.get("status") in ("待核", "不适用") and not f.get("value"):
        return f"[{f['status']}] {f.get('note', '')}".strip()
    return f.get("value", "")


def cross_compare(ws: Workspace, pids: list[str]) -> dict:
    recs = [load_record(ws, ws.get(p)["pid"]) for p in pids]
    rows = []
    for f in schema_fields():
        vals = [_val(r, f["key"]) for r in recs]
        filled = [v for v in vals if v]
        rows.append({"field": f["key"], "label": f["label"], "section": f["section"], "values": vals,
                     "status": [r["fields"][f["key"]].get("status") for r in recs],
                     "differs": len({re.sub(r"\s+", "", v) for v in filled}) > 1})
    return {"products": [{"pid": r["pid"], "company": r["product"].get("company"), "product_name": r["product"].get("product_name")} for r in recs],
            "rows": rows}


def version_diff(ws: Workspace, old_pid: str, new_pid: str) -> dict:
    old, new = load_record(ws, ws.get(old_pid)["pid"]), load_record(ws, ws.get(new_pid)["pid"])
    fm = field_map()
    changes = []
    for f in schema_fields():
        k = f["key"]
        if k in ("product_name", "version_label", "launch_date", "clause_codes", "data_gaps", "sales_status"):
            continue
        a, b = _val(old, k), _val(new, k)
        if re.sub(r"\s+", "", a) == re.sub(r"\s+", "", b):
            continue
        if not a and not b:
            continue
        ch = {"field": k, "label": fm[k]["label"], "section": fm[k]["section"], "old": a, "new": b,
              "change": "新增" if not a else ("删除/未取得" if not b else "变化"), "direction": "",
              "added": [], "removed": [],
              "old_evidence": old["fields"][k].get("evidence", [])[:2], "new_evidence": new["fields"][k].get("evidence", [])[:2]}
        if a and b:
            ia, ib = _items(a), _items(b)
            ch["added"] = [x for x in ib if x not in ia][:12]
            ch["removed"] = [x for x in ia if x not in ib][:12]
            rule = DIRECTION_RULES.get(k)
            if rule:
                fn, up, down = rule
                va, vb = fn(a), fn(b)
                if va is not None and vb is not None and va != vb:
                    ch["direction"] = (up if vb > va else down) + f"（{_fmt(va)}→{_fmt(vb)}，候选判断）"
        changes.append(ch)
    return {"old": {"pid": old["pid"], "product_name": old["product"].get("product_name"), "launch": _val(old, "launch_date")},
            "new": {"pid": new["pid"], "product_name": new["product"].get("product_name"), "launch": _val(new, "launch_date")},
            "changes": changes}


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) else f"{v:g}"


def auto_version_pairs(ws: Workspace, pids: list[str]) -> list[tuple[str, str]]:
    """同公司、同系列（series）的产品按上市时间排序，两两相邻配对。"""
    groups: dict[tuple, list] = {}
    for pid in pids:
        p = ws.get(pid)
        series = p.get("series_line") or p.get("series")
        if not series:
            continue
        rec = load_record(ws, p["pid"])
        launch = _val(rec, "launch_date") or p.get("launch") or ""
        m = re.search(r"20\d\d(?:-\d{2})?", launch)
        groups.setdefault((p["company"], series), []).append((m.group(0) if m else "9999", p["pid"]))
    pairs = []
    for _, lst in groups.items():
        lst.sort()
        pairs += [(lst[i][1], lst[i + 1][1]) for i in range(len(lst) - 1)]
    return pairs


def run_compare(ws: Workspace, pids: list[str] | None = None, pairs: list[tuple[str, str]] | None = None) -> dict:
    pids = [ws.get(p)["pid"] for p in (pids or list(ws.products()))]
    pairs = [(ws.get(a)["pid"], ws.get(b)["pid"]) for a, b in pairs] if pairs else auto_version_pairs(ws, pids)
    out = {"generated_at": now_iso(), "cross": cross_compare(ws, pids), "versions": [version_diff(ws, a, b) for a, b in pairs]}
    write_json(ws.outputs_dir / "对比结果.json", out)
    return out
