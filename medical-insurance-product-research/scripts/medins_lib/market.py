"""全网核验：判断创新时对照整个市场，而不只是内置样本。

对每个需要研究者判断的候选特征（样本内首见 / 少见差异化 / 样本不足），Agent 用自己的联网搜索在全市场检索同类产品：
- 找到：`market add` 记录产品、链接与页面原文；脚本下载页面（或读取手动保存的文件）并核验原文确实出现在页面中
- 没找到：`market none` 记录至少 MIN_QUERIES 组检索词与查过的来源，形成“全网检索未见同类（截至某日）”的可复核记录
记录保存在 <工作区>/market_scan.json，页面副本与抽取文本保存在 <工作区>/market/。innovate 会把命中的产品计入对照样本。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from .common import MedinsError, now_iso, read_json, sha256_file, write_json, write_text
from .normalize import core_key
from .workspace import Workspace

MIN_QUERIES = 2


def scan_path(ws: Workspace) -> Path:
    return ws.root / "market_scan.json"


def load_scan(ws: Workspace) -> dict:
    d = read_json(scan_path(ws), None)
    return d if isinstance(d, dict) and "features" in d else {"schema": "medins-market-scan/1", "features": {}}


def save_scan(ws: Workspace, d: dict) -> None:
    write_json(scan_path(ws), d)


def signature(ws: Workspace) -> str:
    """market_scan.json 的内容指纹：变化后需要重新运行 innovate。"""
    d = load_scan(ws).get("features", {})
    return hashlib.sha1(json.dumps(d, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12] if d else ""


def queries_for(feature: dict) -> list[str]:
    q = list(feature.get("market_queries") or [])
    if not q:
        first = (feature.get("patterns") or [feature["label"]])[0]
        q = [f"{feature['label']} 医疗险", f"{first} 医疗保险 条款"]
    return q


def pending_features(ws: Workspace) -> list[dict]:
    """需要全网核验、但还没有记录（命中或检索未见）的特征。"""
    from .innovation import is_common, load_features
    inn = read_json(ws.outputs_dir / "创新点候选.json", {}) or {}
    fm = {f["key"]: f for f in load_features()}
    done = set(load_scan(ws).get("features", {}))
    out, seen = [], set()
    for info in (inn.get("products") or {}).values():
        for c in info["candidates"]:
            k = c["feature"]
            if k in seen or k in done or k not in fm or is_common(c["verdict"]):
                continue
            seen.add(k)
            out.append(fm[k])
    return out


def _page_copy(ws: Workspace, url: str, file: str | None) -> tuple[Path, Path, str]:
    """下载（或复制手动保存的）页面到 <工作区>/market/，抽取文本，返回 (原件, 文本, 扩展名)。"""
    from .fetch import http_get, sniff
    from .textextract import extract_pages, pages_to_text
    d = ws.root / "market"
    d.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="medins_mk_")) / "page"
    try:
        if file:
            src = Path(file)
            if not src.exists():
                raise MedinsError(f"文件不存在：{file}")
            shutil.copyfile(src, tmp)
        else:
            http_get(url, tmp, timeout=30, retries=2)
        ext = sniff(tmp)
        if ext not in (".pdf", ".html", ".docx", ".xlsx", ".txt", ".json"):
            raise MedinsError(f"页面类型 {ext} 无法抽取文字（图片请改用页面原文或条款 PDF）")
        h = sha256_file(tmp)
        dest = d / f"{h[:16]}{ext}"
        shutil.copyfile(tmp, dest)
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    txt = dest.with_name(dest.stem + "_text.txt")
    if not txt.exists():
        write_text(txt, pages_to_text(extract_pages(dest)["pages"]))
    return dest, txt, ext


def add_hit(ws: Workspace, feature_key: str, product: str, url: str = "", quote: str = "", launch: str = "",
            file: str | None = None, note: str = "", allow_unverified: bool = False) -> dict:
    from .evidence import verify_quote
    from .fetch import suggest_evidence_level
    from .innovation import load_features
    fm = {f["key"]: f for f in load_features()}
    if feature_key not in fm:
        raise MedinsError(f"未知特征：{feature_key}（可选：{'、'.join(fm)}）")
    if not product or not quote or not (url or file):
        raise MedinsError("需要 --product、--quote，以及 --url 或 --file（手动保存的页面/条款）")
    hit = {"product": product, "url": url, "quote": quote, "launch": launch, "note": note, "checked_at": now_iso()}
    try:
        orig, txt, ext = _page_copy(ws, url, file)
        r = verify_quote(quote, txt)
        hit.update({"file": orig.relative_to(ws.root).as_posix(), "sha256": sha256_file(orig), "match": r["match"], "page": r["page"],
                    "score": r["score"]})
        company = product.split("·", 1)[0] if "·" in product else ""
        level, why = suggest_evidence_level(url, "01" if ext in (".pdf", ".docx") else "", company, ext) if url else ("D", "手动保存的页面，无链接")
        hit.update({"evidence_level": level, "level_note": why})
        if r["match"] not in ("exact", "fuzzy") and not allow_unverified:
            raise MedinsError(f"引文未在页面中找到（{r['match']}，相似度 {r['score']}）。请从页面原文逐字复制；"
                              "页面是动态加载的，就在浏览器中另存为 .html/.pdf 后用 --file 传入")
    except MedinsError as e:
        if not allow_unverified:
            raise
        hit.update({"match": "未核验", "error": str(e), "evidence_level": "D", "level_note": "页面无法抓取或引文未核验"})
    d = load_scan(ws)
    f = d["features"].setdefault(feature_key, {"label": fm[feature_key]["label"], "hits": [], "queries": []})
    f["hits"] = [h for h in f.get("hits", []) if not same_product(h["product"], product)] + [hit]
    f["status"] = "found"
    f["updated_at"] = now_iso()
    save_scan(ws, d)
    return hit


def mark_none(ws: Workspace, feature_key: str, queries: list[str], note: str = "") -> dict:
    from .innovation import load_features
    fm = {f["key"]: f for f in load_features()}
    if feature_key not in fm:
        raise MedinsError(f"未知特征：{feature_key}")
    queries = [q.strip() for q in queries or [] if q and q.strip()]
    if len(set(queries)) < MIN_QUERIES:
        raise MedinsError(f"“检索未见”至少需要 {MIN_QUERIES} 组不同的检索词（--query 可多次），并在 --note 写明查过哪些来源")
    if not note.strip():
        raise MedinsError("请用 --note 写明查过哪些来源（如保险公司官网、行业协会产品库、持牌中介平台条款）")
    d = load_scan(ws)
    f = d["features"].setdefault(feature_key, {"label": fm[feature_key]["label"], "hits": [], "queries": []})
    if f.get("hits"):
        raise MedinsError(f"该特征已记录 {len(f['hits'])} 款同类产品，不能再标“检索未见”")
    f.update({"status": "none", "queries": queries, "note": note, "updated_at": now_iso()})
    save_scan(ws, d)
    return f


def same_product(a: str, b: str) -> bool:
    """名称归一后相同，或只差前面的公司名（“太保健康·蓝医保…” 与 “蓝医保…”）。"""
    ka, kb = core_key(a), core_key(b)
    return bool(ka and kb) and (ka == kb or ka.endswith(kb) or kb.endswith(ka))


def hits_for(ws: Workspace, feature_key: str, exclude: set[str]) -> tuple[dict | None, list[dict]]:
    """返回 (该特征的核验记录, 去掉研究对象自身后的命中产品)。"""
    f = load_scan(ws).get("features", {}).get(feature_key)
    if not f:
        return None, []
    return f, [h for h in f.get("hits", []) if not any(same_product(h["product"], x) for x in exclude)]


def describe(m: dict) -> str:
    """候选特征的全网核验摘要（用于对比表、报告、PPT 与 analysis 骨架）。"""
    if not m or not m.get("status"):
        return "尚未全网核验"
    if m.get("hits"):
        return f"全网核验命中 {m['hits']} 款同类（{m.get('checked_at', '')}）"
    return f"全网检索未见同类（截至 {m.get('checked_at', '')}；{len(m.get('queries', []))} 组检索词）"


def host(url: str) -> str:
    return (urlparse(url).hostname or "") if url else ""
