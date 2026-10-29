"""校验：资料完整度、版本核对、逐条引文复核、取值与引文数值一致性、口径与逻辑一致性。

error  = 不能进入结论/报告正文的问题（引文找不到、必填字段未处理、待复核未清理、推定无依据）
warning = 需要人工留意（规则抽取未确认、近似匹配、数字未在引文中出现、只有 C/D 级证据、版本未核对）
"""
from __future__ import annotations

import re

from .common import now_iso, write_json
from .doctypes import DOC_TYPES
from .fetch import completeness, load_manifest
from .normalize import unmatched_numbers
from .records import field_map, load_record, verify_evidence
from .workspace import Workspace


def validate_product(ws: Workspace, pid: str, reverify: bool = True) -> dict:
    p = ws.get(pid)
    pid = p["pid"]
    errors, warnings, info = [], [], []
    m = load_manifest(ws, pid)
    comp = completeness(m)
    for code, st in comp.items():
        if st.startswith("缺失"):
            errors.append(f"资料：{code} {DOC_TYPES[code]} 缺失且未登记原因（找不到请用 `gap` 登记“未找到可确认文件/待取得原件”）")
        elif st in ("未找到可确认文件", "待取得原件"):
            warnings.append(f"资料：{code} {DOC_TYPES[code]} — {st}")
    for d in m["documents"]:
        vc = d.get("version_check", {})
        if vc.get("status") == "unverified" and d["doc_type"] in ("01", "02", "03", "04", "05"):
            warnings.append(f"版本：{d['file']} 未核对到已登记的备案条款名/产品名"
                            f"（{vc.get('year_note') or '请登记 clause_names 后 verify-docs，或人工确认属于本版本'}）")
        elif vc.get("year_note") and vc.get("status") == "partial":
            warnings.append(f"版本：{d['file']} {vc['year_note']}")
        if d.get("scanned"):
            warnings.append(f"文本：{d['file']} 疑似扫描件，引文核验不可用")
        if d.get("warning") and not d.get("scanned"):
            warnings.append(f"文本：{d['file']} {d['warning']}")
    if not p.get("clause_names"):
        warnings.append("版本：未登记备案条款名（`register --clause-name`），无法自动核对条款/费率表是否属于本版本")

    rec = load_record(ws, pid)
    fm = field_map()
    changed = False
    stats: dict[str, int] = {}
    for key, f in rec["fields"].items():
        label = fm.get(key, {}).get("label", key)
        st = f.get("status", "未填")
        stats[st] = stats.get(st, 0) + 1
        if st == "未填":
            if fm.get(key, {}).get("required"):
                errors.append(f"字段：{label} 未处理（必填；取不到请标“待核”并写原因）")
            continue
        if st == "待复核":
            errors.append(f"字段：{label} 引文未核验（待复核）")
        if st in ("推定", "待核") and not f.get("note"):
            errors.append(f"字段：{label} 状态“{st}”缺少依据/原因 note")
        if st == "规则抽取":
            warnings.append(f"字段：{label} 为规则抽取结果，待研究者确认：{f.get('value', '')[:40]}")
        if reverify and f.get("evidence"):
            new_evs = [verify_evidence(ws, pid, e) for e in f["evidence"]]
            if [e.get("match") for e in new_evs] != [e.get("match") for e in f["evidence"]]:
                changed = True
            f["evidence"] = new_evs
        bad = [e for e in f.get("evidence", []) if e.get("match") not in ("exact", "fuzzy")]
        if bad and st in ("原文核验", "规则抽取", "推定"):
            errors.append(f"字段：{label} 有 {len(bad)} 条引文在原文中找不到：" + "；".join(
                f"{e.get('doc')}「{(e.get('quote') or '')[:24]}」" for e in bad[:3]))
        fuzzy = [e for e in f.get("evidence", []) if e.get("match") == "fuzzy"]
        if fuzzy:
            warnings.append(f"字段：{label} 有 {len(fuzzy)} 条引文为近似匹配（PDF 断行/脚注），请人工过目")
        if st in ("原文核验", "规则抽取", "推定") and f.get("value"):
            from .guide import lint_field
            warnings += [f"细节：{x}" for x in lint_field(key, f["value"])]
        if st in ("原文核验", "规则抽取") and f.get("value"):
            miss = unmatched_numbers(f["value"], " ".join(e.get("quote", "") for e in f.get("evidence", [])))
            if miss:
                warnings.append(f"字段：{label} 取值中的数字 {','.join(miss)} 未在引文中出现")
            levels = {e.get("evidence_level") for e in f.get("evidence", [])}
            if levels and levels <= {"C", "D"}:
                warnings.append(f"字段：{label} 仅有 C/D 级来源，结论须标注“待正式原件核实”")
    for row in rec.get("benefits") or []:
        if row.get("status") == "待复核":
            errors.append(f"责任明细：{row.get('name')} 引文未核验")
    for t in rec.get("rate_tables") or []:
        if t.get("status") == "待复核":
            errors.append(f"费率表：{t.get('basis')} 引文未核验")

    F = rec["fields"]
    hdc = F["hd_count"].get("value", "")
    if "免健告" in F["access_channel"].get("value", "") and hdc and not re.match(r"^\s*(0|无|零)", hdc):
        warnings.append("逻辑：健告类型为免健告，但健告条数不为 0，请核对")
    if F["premium_50m"].get("value") and F["premium_basis"].get("status") == "未填":
        errors.append("口径：已填50岁保费但未写保费口径（年龄档/社保/首续/计划/免赔/是否含附加险）")
    ld = F["launch_date"].get("value", "")
    if ld and not re.search(r"20\d\d(-\d{2})?", ld):
        warnings.append("口径：上市时间建议写成 YYYY-MM 并注明依据")
    if "保证续保" in F["term"].get("value", ""):
        warnings.append("口径：“保险期间”字段里出现“保证续保”，二者应分开填写")
    if changed:
        from .records import save_record
        save_record(ws, rec)
    return {"pid": pid, "product": p["product_name"], "company": p["company"], "errors": errors, "warnings": warnings,
            "info": info, "completeness": comp, "field_status": stats}


def validate_all(ws: Workspace, pids: list[str] | None = None) -> dict:
    pids = pids or list(ws.products())
    res = [validate_product(ws, pid) for pid in pids]
    report = {"generated_at": now_iso(), "products": res,
              "totals": {"errors": sum(len(r["errors"]) for r in res), "warnings": sum(len(r["warnings"]) for r in res)}}
    write_json(ws.outputs_dir / "校验报告.json", report)
    return report
