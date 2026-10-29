"""统一证据编号（E1、E2…）：Excel 证据索引与分析报告引用同一套编号。"""
from __future__ import annotations

from .records import field_map, load_record
from .workspace import Workspace


def build_index(ws: Workspace, pids: list[str]) -> tuple[list[dict], dict]:
    """返回 (证据列表, 映射)。映射键：(pid, 字段, 序号) / (pid, 'benefit', 行, 序号)。"""
    fm = field_map()
    items, idx = [], {}
    n = 0
    for pid in pids:
        p = ws.get(pid)
        rec = load_record(ws, p["pid"])
        for key in fm:
            f = rec["fields"].get(key, {})
            for i, e in enumerate(f.get("evidence", [])):
                n += 1
                eid = f"E{n}"
                idx[(p["pid"], key, i)] = eid
                items.append({"id": eid, "pid": p["pid"], "company": p["company"], "product": p["product_name"],
                              "field": key, "label": fm[key]["label"], "value": f.get("value", ""), "status": f.get("status"),
                              **{k: e.get(k) for k in ("doc", "page", "quote", "match", "score", "evidence_level", "url", "doc_type")},
                              "archive_dir": p["archive_dir"]})
        for r_i, row in enumerate(rec.get("benefits") or []):
            for i, e in enumerate(row.get("evidence", [])):
                n += 1
                eid = f"E{n}"
                idx[(p["pid"], "benefit", r_i, i)] = eid
                items.append({"id": eid, "pid": p["pid"], "company": p["company"], "product": p["product_name"],
                              "field": "benefits", "label": f"责任明细·{row.get('name')}{('·' + row['plan']) if row.get('plan') else ''}",
                              "value": " / ".join(str(row.get(k) or "") for k in ("limit", "deductible", "ratio")), "status": row.get("status"),
                              **{k: e.get(k) for k in ("doc", "page", "quote", "match", "score", "evidence_level", "url", "doc_type")},
                              "archive_dir": p["archive_dir"]})
        for t_i, t in enumerate(rec.get("rate_tables") or []):
            evs = t.get("evidence", [])
            if evs:
                n += 1
                eid = f"E{n}"
                idx[(p["pid"], "rate", t_i)] = eid
                e = evs[0]
                items.append({"id": eid, "pid": p["pid"], "company": p["company"], "product": p["product_name"],
                              "field": "rate_table", "label": f"费率表·{t.get('basis')}", "value": f"{len(t.get('rows', []))}个年龄档（逐行引文{len(evs)}条）",
                              "status": t.get("status"), **{k: e.get(k) for k in ("doc", "page", "match", "score", "evidence_level", "url", "doc_type")},
                              "quote": f"{e.get('quote')} …（共{len(evs)}行）", "archive_dir": p["archive_dir"]})
    return items, idx


def refs_for(idx: dict, pid: str, field: str) -> list[str]:
    out, i = [], 0
    while (pid, field, i) in idx:
        out.append(idx[(pid, field, i)])
        i += 1
    return out


def fmt_refs(ids: list[str]) -> str:
    return "".join(f"[{x}]" for x in ids)
