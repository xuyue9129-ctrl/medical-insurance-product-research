"""标准化字段记录：每个取值都挂原文引文（文件 + 页码 + 逐字引文 + 来源链接），写入前即核验。

字段状态：
- 原文核验：引文已在归档原件的抽取文本中逐字（或高相似度）找到
- 规则抽取：脚本按规则从原文抽出（引文天然可核验），但含义尚待研究者确认
- 推定：研究者基于原文的归纳/推断，必须写 note 说明依据（如“问询外可赔”类结论）
- 待复核：引文暂未能在原文中找到（仅在 --allow-unverified 时出现，不能进入结论）
- 待核：未取得资料或无法确认，note 写明原因（如“健告逐题原文未公开”）
- 不适用：该产品无此项（如免健告产品的健告条数写 0 时可用原文核验，也可标不适用）
- 未填：尚未处理
"""
from __future__ import annotations

from pathlib import Path

from .common import MedinsError, load_asset_json, now_iso, read_json, write_json
from .evidence import verify_quote
from .normalize import name_key, unmatched_numbers
from .workspace import Workspace

STATUSES = ["原文核验", "规则抽取", "推定", "待复核", "待核", "不适用", "未填"]
FILLED = {"原文核验", "规则抽取", "推定"}
NEEDS_NOTE = {"推定", "待核"}


def load_schema() -> dict:
    return load_asset_json("schema/fields.json", {}) or {}


def schema_fields() -> list[dict]:
    return load_schema().get("fields", [])


def field_map() -> dict[str, dict]:
    return {f["key"]: f for f in schema_fields()}


def resolve_field(key_or_label: str) -> str:
    fm = field_map()
    if key_or_label in fm or key_or_label in ("benefits", "rate_table"):
        return key_or_label
    k = name_key(key_or_label)
    for f in fm.values():
        if name_key(f["label"]) == k:
            return f["key"]
    raise MedinsError(f"未知字段：{key_or_label}（`medins.py fields` 查看全部字段）")


def new_record(product: dict) -> dict:
    return {
        "schema": "medins-record/1", "pid": product["pid"],
        "product": {k: product.get(k) for k in ("company", "insurer_full", "product_name", "series", "version",
                                                "clause_names", "body_type", "launch")},
        "fields": {f["key"]: {"value": "", "status": "未填", "evidence": [], "note": ""} for f in schema_fields()},
        "benefits": [], "rate_table": None, "drafts": {}, "hints": {}, "created_at": now_iso(),
    }


def load_record(ws: Workspace, pid: str, create: bool = True) -> dict:
    p = ws.get(pid)
    rec = read_json(ws.record_path(p["pid"]))
    if rec is None:
        if not create:
            raise MedinsError(f"尚无记录：{p['product_name']}（先运行 extract）")
        rec = new_record(p)
    for f in schema_fields():  # schema 升级后补齐新字段
        rec["fields"].setdefault(f["key"], {"value": "", "status": "未填", "evidence": [], "note": ""})
    rec["product"].update({k: p.get(k) for k in ("company", "insurer_full", "product_name", "series", "version",
                                                 "clause_names", "body_type", "launch") if p.get(k)})
    return rec


def save_record(ws: Workspace, rec: dict) -> None:
    rec["updated_at"] = now_iso()
    write_json(ws.record_path(rec["pid"]), rec)


# ---------------- 证据 ----------------

def find_doc(ws: Workspace, pid: str, doc: str) -> dict:
    """按文件名（可写前缀或片段，如“01_”“费率表”）找到归档文档条目。"""
    from .fetch import load_manifest
    docs = load_manifest(ws, pid)["documents"]
    exact = [d for d in docs if d["file"] == doc]
    if exact:
        return exact[0]
    k = name_key(doc)
    hits = [d for d in docs if k and (k in name_key(d["file"]) or k in name_key(d.get("title", "")))]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise MedinsError(f"归档中找不到文件“{doc}”；可用：" + "；".join(d["file"] for d in docs))
    raise MedinsError(f"“{doc}”匹配到多个文件：" + "；".join(d["file"] for d in hits) + "（请写完整文件名）")


def verify_evidence(ws: Workspace, pid: str, ev: dict) -> dict:
    ev = dict(ev)
    quote = (ev.get("quote") or "").strip()
    if not ev.get("doc"):
        ev.update({"match": "doc_missing", "detail": "未指定来源文件"})
        return ev
    try:
        d = find_doc(ws, pid, ev["doc"])
    except MedinsError as e:
        ev.update({"match": "doc_missing", "detail": str(e)})
        return ev
    ev["doc"] = d["file"]
    ev["doc_type"] = d["doc_type"]
    ev["evidence_level"] = d.get("evidence_level", "")
    srcs = [s.get("url") for s in d.get("sources", []) if s.get("url")]
    ev["url"] = ev.get("url") or (srcs[0] if srcs else "")
    ev["sha256"] = d.get("sha256", "")[:16]
    if d.get("version_check", {}).get("status") == "unverified":
        ev["version_warning"] = "来源文件版本未核对（未见已登记备案条款名）"
    if not quote:
        ev.update({"match": "empty", "detail": "缺少引文"})
        return ev
    if not d.get("text_file"):
        ev.update({"match": "no_text", "detail": "该文件无可检索文本（扫描件/图片），需人工核对"})
        return ev
    r = verify_quote(quote, ws.root / d["text_file"], ev.get("page"))
    ev.update({"match": r["match"], "page": r["page"] if r["match"] in ("exact", "fuzzy") else ev.get("page"),
               "score": r["score"], "verified_at": now_iso()})
    return ev


def _ok(ev: dict) -> bool:
    return ev.get("match") in ("exact", "fuzzy")


def apply_item(ws: Workspace, rec: dict, item: dict, allow_unverified: bool = False) -> dict:
    """写入单个字段；返回 {field, applied, status, problems, warnings}。"""
    pid = rec["pid"]
    key = resolve_field(item.get("field", ""))
    if key == "benefits":
        return _apply_benefits(ws, rec, item, allow_unverified)
    if key == "rate_table":
        return _apply_rate_table(ws, rec, item, allow_unverified)
    status = item.get("status") or ""
    value = str(item.get("value") or "").strip()
    note = str(item.get("note") or "").strip()
    evs = [verify_evidence(ws, pid, e) for e in (item.get("evidence") or [])]
    problems, warnings = [], []
    if status and status not in STATUSES:
        problems.append(f"状态无效：{status}")
    if status in NEEDS_NOTE and not note:
        problems.append(f"状态为“{status}”时必须写 note（依据或缺失原因）")
    if status in ("待核", "不适用"):
        pass
    elif status == "推定":
        if not value:
            problems.append("推定值不能为空")
        bad = [e for e in evs if not _ok(e)]
        if bad:
            problems.append("推定所附引文未在原文中找到：" + "；".join(f"{e.get('doc')}「{e.get('quote', '')[:30]}」" for e in bad))
    else:
        if not value:
            problems.append("取值为空（无法取得请用 status=待核 并写原因）")
        if not evs:
            problems.append("缺少原文引文 evidence（至少一条：doc + quote）")
        bad = [e for e in evs if not _ok(e)]
        if bad:
            msg = "；".join(f"{e.get('doc')}「{(e.get('quote') or '')[:40]}」→{e.get('match')}{'(' + str(e.get('score')) + ')' if e.get('score') else ''}"
                           f"{' ' + e['detail'] if e.get('detail') else ''}" for e in bad)
            if allow_unverified:
                warnings.append("引文未核验：" + msg)
                status = "待复核"
            else:
                problems.append("引文未在原文中找到：" + msg)
        if not problems and status != "待复核":
            status = status if status == "规则抽取" else "原文核验"
    if value and evs and status in ("原文核验", "规则抽取", "待复核"):
        miss = unmatched_numbers(value, " ".join(e.get("quote", "") for e in evs))
        if miss:
            warnings.append(f"取值中的数字 {','.join(miss)} 未出现在引文里：请核对是否换算/笔误，或补充引文")
    for e in evs:
        if e.get("version_warning"):
            warnings.append(f"{e['doc']}：{e['version_warning']}")
        if e.get("match") == "fuzzy":
            warnings.append(f"{e['doc']} 第{e.get('page')}页为近似匹配（{e.get('score')}），请人工复核引文")
        if e.get("evidence_level") in ("C", "D"):
            warnings.append(f"{e['doc']} 证据等级{e['evidence_level']}：仅可作线索，结论须以正式原件为准")
    warnings = list(dict.fromkeys(warnings))
    if problems:
        return {"field": key, "applied": False, "problems": problems, "warnings": warnings}
    rec["fields"][key] = {"value": value, "status": status or "原文核验", "evidence": evs, "note": note,
                          "checks": warnings, "updated_at": now_iso(), "by": item.get("by", "agent")}
    return {"field": key, "applied": True, "status": rec["fields"][key]["status"], "problems": [], "warnings": warnings}


def _verify_rows_evidence(ws, pid, evs, allow_unverified):
    evs = [verify_evidence(ws, pid, e) for e in (evs or [])]
    bad = [e for e in evs if not _ok(e)]
    return evs, bad


def _apply_benefits(ws: Workspace, rec: dict, item: dict, allow_unverified: bool) -> dict:
    rows_in = item.get("rows") or []
    problems, warnings, rows = [], [], []
    for i, r in enumerate(rows_in, 1):
        if not r.get("name"):
            problems.append(f"第{i}行缺少责任名称 name")
            continue
        evs, bad = _verify_rows_evidence(ws, rec["pid"], r.get("evidence"), allow_unverified)
        if not evs:
            problems.append(f"{r['name']}：缺少原文引文")
        elif bad and not allow_unverified:
            problems.append(f"{r['name']}：引文未找到 " + "；".join(f"「{(e.get('quote') or '')[:30]}」" for e in bad))
        elif bad:
            warnings.append(f"{r['name']}：引文未核验")
        nums = " ".join(str(r.get(k, "")) for k in ("limit", "deductible", "ratio"))
        miss = unmatched_numbers(nums, " ".join(e.get("quote", "") for e in evs))
        if miss:
            warnings.append(f"{r['name']}：数字 {','.join(miss)} 未出现在引文里")
        row = {k: r.get(k, "") for k in ("name", "plan", "limit", "deductible", "ratio", "scope")}
        row.update({"evidence": evs, "status": "待复核" if bad else "原文核验"})
        rows.append(row)
    if problems:
        return {"field": "benefits", "applied": False, "problems": problems, "warnings": warnings}
    if item.get("mode") == "append":
        rec["benefits"] = rec.get("benefits", []) + rows
    else:
        rec["benefits"] = rows
    return {"field": "benefits", "applied": True, "status": f"{len(rows)}行", "problems": [], "warnings": warnings}


def _apply_rate_table(ws: Workspace, rec: dict, item: dict, allow_unverified: bool) -> dict:
    t = item.get("table") or {}
    problems, warnings = [], []
    if not t.get("basis"):
        problems.append("费率表缺少口径 basis（社保/交费方式/等待期/首续/计划）")
    if not t.get("columns") or not t.get("rows"):
        problems.append("费率表缺少 columns 或 rows")
    evs, bad = _verify_rows_evidence(ws, rec["pid"], t.get("evidence") or item.get("evidence"), allow_unverified)
    if not evs:
        problems.append("费率表缺少原文引文（可用 `rates import` 自动生成逐行引文）")
    elif bad and not allow_unverified:
        problems.append("费率表引文未找到：" + "；".join(f"「{(e.get('quote') or '')[:30]}」" for e in bad[:5]))
    if problems:
        return {"field": "rate_table", "applied": False, "problems": problems, "warnings": warnings}
    tables = rec.get("rate_tables") or []
    t = {**t, "evidence": evs, "status": "待复核" if bad else "原文核验", "updated_at": now_iso()}
    tables = [x for x in tables if x.get("basis") != t["basis"]] + [t]
    rec["rate_tables"] = tables
    rec["rate_table"] = tables[0]
    return {"field": "rate_table", "applied": True, "status": f"{len(t['rows'])}行×{len(t['columns'])}列", "problems": [],
            "warnings": warnings}


def apply_patch(ws: Workspace, pid: str, items: list[dict], allow_unverified: bool = False) -> list[dict]:
    rec = load_record(ws, pid)
    results = [apply_item(ws, rec, it, allow_unverified) for it in items]
    save_record(ws, rec)
    return results


def load_patch(path: Path) -> list[dict]:
    data = read_json(Path(path))
    if isinstance(data, dict):
        data = data.get("items") or data.get("fields") or [data]
    if not isinstance(data, list):
        raise MedinsError("补丁文件须为 JSON 数组（或 {\"items\": [...]}）")
    from .guide import clean_patch_items  # 允许直接 apply `template` 生成的骨架（自动忽略说明键与未填项）
    return clean_patch_items(data)


def summary(rec: dict) -> dict:
    fm = field_map()
    counts: dict[str, int] = {}
    missing_required = []
    for k, f in rec["fields"].items():
        counts[f["status"]] = counts.get(f["status"], 0) + 1
        if fm.get(k, {}).get("required") and f["status"] == "未填":
            missing_required.append(fm[k]["label"])
    return {"pid": rec["pid"], "product": rec["product"].get("product_name"), "status_counts": counts,
            "missing_required": missing_required, "benefit_rows": len(rec.get("benefits") or []),
            "rate_tables": len(rec.get("rate_tables") or [])}
