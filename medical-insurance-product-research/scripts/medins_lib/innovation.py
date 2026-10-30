"""创新点识别：特征打标 + 市场基线对照 + 版本新增判断，生成“创新点候选”，由研究者给出最终判断。

基线（对照样本）=
  1. 本工作区其他研究产品的标准化记录
  2. 同业对照产品（register --role peer 登记，只整理与候选特征相关的字段，取值同样经过原文核验）
  3. 样本摘要目录（~/.medins-research/catalog.json 或工作区 catalog_user.json 中带 overview 的产品，可选）

判定（候选），某特征“有可比字段的对照产品数”记为 N：
- N ≥ MIN_PEERS：样本内首见（无对照产品具备）/ 少见差异化（≤2 款或 ≤10%）/ 行业常见/跟进（其余）；
  与行业先验冲突时（如样本判“首见”但先验为“普遍”）给出提示
- N < MIN_PEERS：
  · 先验为“普遍/较常见” → 行业常见/跟进（先验）
  · 小样本中已有 ≥2 款对照产品具备 → 行业常见/跟进（小样本）
  · 其余 → 样本不足·待补对照（next 会引导补充同业对照产品，或说明原因后跳过）
- 本系列新增：相对同系列上一版本新出现（来自版本对比）
“首见”只代表样本内，不代表全市场；营销稿/媒体（C/D 级）支撑的特征一律标“待正式条款核实”。
"""
from __future__ import annotations

import re

from .catalog import load_catalog
from .common import load_asset_json, now_iso, read_json, write_json
from .compare import auto_version_pairs
from .normalize import core_key, name_key
from .records import load_record
from .workspace import Workspace

MIN_PEERS = 5
INSUFFICIENT = "样本不足·待补对照"
ORDER = {"样本内首见": 0, "少见差异化": 1, INSUFFICIENT: 2}


def load_features() -> list[dict]:
    return (load_asset_json("schema/features.json", {}) or {}).get("features", [])


def is_common(verdict: str) -> bool:
    return str(verdict or "").startswith("行业常见")


def needs_judgment(c: dict) -> bool:
    """需要研究者逐条判断的候选：非行业常见，或属于本系列新增。"""
    return not is_common(c.get("verdict")) or bool(c.get("new_in_series"))


def _has(feature: dict, texts: list[str]) -> tuple[bool, str]:
    pats = [re.compile(p, re.I) for p in feature.get("patterns", [])]
    negs = [re.compile(p, re.I) for p in feature.get("negative", [])]
    for t in texts:
        t = (t or "").strip()
        if not t or any(n.search(t) for n in negs):
            continue
        for p in pats:
            m = p.search(t)
            if m:
                return True, t[max(0, m.start() - 20): m.end() + 30]
    return False, ""


def record_texts(rec: dict, fields: list[str]) -> list[str]:
    out = []
    for k in fields:
        f = rec["fields"].get(k, {})
        if f.get("status") in ("原文核验", "规则抽取", "推定", "待复核") and f.get("value"):
            out.append(f["value"])
    return out


def baseline_entries(ws: Workspace, exclude_names: set[str]) -> list[dict]:
    out = []
    for e in load_catalog(ws.root):
        if not e.get("overview"):
            continue
        if core_key(e["product_name"]) in exclude_names:
            continue
        out.append({"name": f"{e['company']}·{e['product_name']}", "launch": e.get("launch") or e["overview"].get("launch_date", ""),
                    "source": f"样本摘要目录（{e.get('overview_as_of', '')}）", "texts": e["overview"]})
    return out


def _launch_key(s: str) -> str:
    m = re.search(r"(20\d\d)(?:-(\d{2}))?", s or "")
    return f"{m.group(1)}-{m.group(2) or '12'}" if m else "9999-99"


def _verdict(feature: dict, n: int, total: int) -> tuple[str, list[str]]:
    prior, flags = feature.get("prior"), []
    share = n / total if total else 0.0
    if total >= MIN_PEERS:
        verdict = "样本内首见" if n == 0 else "少见差异化" if (n <= 2 or share <= 0.10) else "行业常见/跟进"
        if prior in ("普遍", "较常见") and not is_common(verdict):
            flags.append(f"与行业先验（{prior}：{feature.get('prior_note', '')}）不一致，可能是对照样本漏记，下结论前请补同业原文核实")
        return verdict, flags
    if prior in ("普遍", "较常见"):
        return "行业常见/跟进（先验）", [f"对照样本仅 {total} 款，按行业先验判断：{feature.get('prior_note', '')}"]
    if n >= 2:
        return "行业常见/跟进（小样本）", [f"对照样本仅 {total} 款，其中 {n} 款已具备"]
    if prior == "新兴":
        flags.append(f"行业先验：{feature.get('prior_note', '')}")
    flags.append(f"对照样本仅 {total} 款（至少需要 {MIN_PEERS} 款），不能判断是否首见/少见；请补充同业对照产品或在结论边界中说明")
    return INSUFFICIENT, flags


def run_innovation(ws: Workspace, pids: list[str] | None = None) -> dict:
    feats = load_features()
    pids = [ws.get(p)["pid"] for p in (pids or ws.research_pids())]
    peer_ids = [p for p in ws.peer_pids() if p not in pids]
    recs = {pid: load_record(ws, pid) for pid in pids}
    peer_recs = {pid: load_record(ws, pid) for pid in peer_ids}
    names = {pid: core_key(r["product"].get("product_name", "")) for pid, r in {**recs, **peer_recs}.items()}
    base = baseline_entries(ws, set(names.values()))
    pairs = auto_version_pairs(ws, pids)
    prev_of = {new: old for old, new in pairs}

    results = {}
    for pid, rec in recs.items():
        launch = rec["fields"]["launch_date"].get("value") or rec["product"].get("launch") or ""
        items = []
        for f in feats:
            has, snippet = _has(f, record_texts(rec, f["fields"]))
            if not has:
                continue
            peers_with, peers_total = [], 0
            for b in base:
                texts = [b["texts"].get(k, "") for k in f.get("overview", [])]
                if not any(texts):
                    continue
                peers_total += 1
                ok, snip = _has(f, texts)
                if ok:
                    peers_with.append({"name": b["name"], "launch": b["launch"], "source": b["source"], "snippet": snip})
            for opid, orec in list(recs.items()) + list(peer_recs.items()):
                if opid == pid:
                    continue
                texts = record_texts(orec, f["fields"])
                if not texts:
                    continue
                peers_total += 1
                ok, snip = _has(f, texts)
                if ok:
                    peers_with.append({"name": f"{orec['product'].get('company')}·{orec['product'].get('product_name')}",
                                       "launch": orec["fields"]["launch_date"].get("value") or "",
                                       "source": "同业对照（原文核验）" if opid in peer_recs else "本次研究记录", "snippet": snip})
            n = len(peers_with)
            share = n / peers_total if peers_total else 0.0
            verdict, flags = _verdict(f, n, peers_total)
            earliest = min(peers_with, key=lambda x: _launch_key(x["launch"])) if peers_with else None
            earlier_than_all = bool(peers_with) and _launch_key(launch) < _launch_key(earliest["launch"])
            new_in_series = None
            if pid in prev_of:
                old_has, _ = _has(f, record_texts(recs[prev_of[pid]], f["fields"]))
                new_in_series = not old_has
            evid = []
            for k in f["fields"]:
                fld = rec["fields"].get(k, {})
                if fld.get("value") and _has(f, [fld["value"]])[0]:
                    for e in fld.get("evidence", [])[:2]:
                        evid.append({"field": k, "doc": e.get("doc"), "page": e.get("page"), "quote": e.get("quote"),
                                     "url": e.get("url"), "evidence_level": e.get("evidence_level"), "status": fld.get("status")})
            if f.get("baseline_note") and base:
                flags.append(f["baseline_note"])
            if evid and all(x.get("evidence_level") in ("C", "D") for x in evid):
                flags.append("仅有 C/D 级来源，待正式条款核实")
            if any(x.get("status") in ("规则抽取", "推定", "待复核") for x in evid) or not evid:
                flags.append("取值尚未全部原文核验")
            items.append({
                "feature": f["key"], "label": f["label"], "category": f["category"], "verdict": verdict,
                "prior": f.get("prior", ""), "new_in_series": new_in_series, "earliest_in_sample": earlier_than_all,
                "peers_with": n, "peers_total": peers_total, "share": round(share, 3),
                "peer_examples": sorted(peers_with, key=lambda x: _launch_key(x["launch"]))[:5],
                "product_snippet": snippet, "evidence": evid[:4], "flags": flags,
            })
        items.sort(key=lambda x: (ORDER.get(x["verdict"], 3), not x.get("new_in_series"), x["category"]))
        results[pid] = {"product": rec["product"].get("product_name"), "company": rec["product"].get("company"),
                        "launch": launch, "previous_version": prev_of.get(pid), "candidates": items}
    out = {"generated_at": now_iso(),
           "baseline": {"builtin_samples": len(base), "workspace_products": len(recs), "peer_products": len(peer_recs),
                        "peers": [{"pid": k, "name": f"{r['product'].get('company')}·{r['product'].get('product_name')}"}
                                  for k, r in peer_recs.items()],
                        "min_peers": MIN_PEERS,
                        "note": f"普及度=具备该特征的对照产品/有可比字段的对照产品；对照产品少于 {MIN_PEERS} 款时不判断首见/少见；"
                                "‘首见’仅指样本内，不代表全市场。"},
           "products": results}
    write_json(ws.outputs_dir / "创新点候选.json", out)
    return out


def insufficient_features(ws: Workspace) -> list[dict]:
    """创新点候选中“样本不足·待补对照”的特征（去重），供 next / peers 使用。"""
    inn = read_json(ws.outputs_dir / "创新点候选.json", {}) or {}
    fm = {f["key"]: f for f in load_features()}
    out, seen = [], set()
    for info in (inn.get("products") or {}).values():
        for c in info["candidates"]:
            if c["verdict"] == INSUFFICIENT and c["feature"] not in seen and c["feature"] in fm:
                seen.add(c["feature"])
                out.append(fm[c["feature"]])
    return out


def _fields_of(feats: list[dict]) -> list[str]:
    keys = ["launch_date", "access_channel"]
    for f in feats:
        keys += [k for k in f["fields"] if k not in keys]
    return keys


def peer_fields(ws: Workspace) -> list[str]:
    """同业对照产品需要整理的字段：待补对照特征涉及的记录字段 + 上市时间与准入通道。

    范围记在 workspace.json 的 peer_fields（只增不减），补完对照、特征不再“样本不足”后范围保持不变。
    """
    cfg = ws.load(required=False) or {}
    keys = list(cfg.get("peer_fields") or [])
    lack = insufficient_features(ws)
    if lack:
        merged = keys + [k for k in _fields_of(lack) if k not in keys]
        if merged != keys and cfg:
            cfg["peer_fields"] = merged
            ws.save(cfg)
        return merged
    if keys:
        return keys
    inn = read_json(ws.outputs_dir / "创新点候选.json", {}) or {}
    fm = {f["key"]: f for f in load_features()}
    feats = [fm[c["feature"]] for info in (inn.get("products") or {}).values() for c in info["candidates"]
             if needs_judgment(c) and c["feature"] in fm]
    return _fields_of(feats or load_features())


def suggest_peers(ws: Workspace, limit: int = 10) -> list[dict]:
    """从产品目录中挑选可作同业对照的产品：同产品类型、名称相近（免健告/中高端/百万等）、不同系列，优先有官网链接的。"""
    prods = [ws.get(p) for p in ws.research_pids()]
    have = {name_key(p["product_name"]) for p in ws.products().values()}
    series = {(p.get("series_line") or p.get("series") or "") for p in prods} - {""}
    types = {(p.get("product_type") or "") for p in prods} - {""}
    words = ("免健告", "简易", "易核", "中高端", "百万", "长期", "特需", "带病", "既往症")
    want = {w for p in prods for w in words if w in p["product_name"]}
    out = []
    for e in load_catalog(ws.root):
        if name_key(e["product_name"]) in have or (e.get("series_line") or e.get("series") or "") in series and series:
            continue
        score = 0.0
        if types and e.get("product_type") in types:
            score += 2
        elif "医疗" in (e.get("product_type") or e["product_name"]):
            score += 0.5
        score += sum(1 for w in want if w in e["product_name"])
        score += 1 if e.get("source_urls") else 0
        if e.get("overview"):
            score -= 3  # 已在样本摘要目录中，不必重复整理
        out.append((score, e))
    out.sort(key=lambda x: -x[0])
    picked, seen = [], set()
    for s, e in out:  # 同一系列只推荐一款，避免对照样本扎堆
        key = e.get("series_line") or re.sub(r"（[^）]*）|\([^)]*\)", "", e["product_name"])
        if key in seen:
            continue
        seen.add(key)
        picked.append({"id": e["id"], "company": e["company"], "product_name": e["product_name"], "product_type": e.get("product_type", ""),
                       "source_urls": (e.get("source_urls") or [])[:2], "score": round(s, 1)})
        if len(picked) >= limit:
            break
    return picked
