"""产品目录与模糊检索。

目录来源（后者覆盖前者同 id 条目）：
1. 内置种子目录 assets/catalog/products.json（公开版：产品名、公司、系列、公开链接）
2. 用户级目录 ~/.medins-research/catalog.json（可放自有研究摘要 overview，作为创新点的市场基线，不随仓库分发）
3. 工作区目录 <workspace>/catalog_user.json

匹配思路：公司别名识别 + 字符二元组 Dice + 顺序子序列/包含率 + 编辑相似度，
再按年份/版本/公司一致性加减分；给出候选、分数、匹配依据与是否存在歧义。
"""
from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .common import load_asset_json, read_json, short_hash, write_json
from .normalize import core_key, name_key, versions_in, years_in

def user_catalog_path() -> Path:
    """用户级目录路径（每次调用时读取环境变量 MEDINS_HOME，便于测试与多用户环境）。"""
    return Path(os.environ.get("MEDINS_HOME") or (Path.home() / ".medins-research")) / "catalog.json"


def load_companies() -> list[dict]:
    return load_asset_json("catalog/companies.json", []) or []


def load_catalog(workspace: Path | None = None) -> list[dict]:
    entries: dict[str, dict] = {}
    sources = [load_asset_json("catalog/products.json", []) or [], read_json(user_catalog_path(), []) or []]
    if workspace:
        sources.append(read_json(Path(workspace) / "catalog_user.json", []) or [])
    for src in sources:
        for e in src:
            e = dict(e)
            e.setdefault("id", make_pid(e.get("company", ""), e.get("product_name", "")))
            entries[e["id"]] = {**entries.get(e["id"], {}), **e}
    return list(entries.values())


def make_pid(company: str, product_name: str) -> str:
    return "p" + short_hash(name_key(company), name_key(product_name), n=8)


def add_to_catalog(entry: dict, workspace: Path | None = None, user_level: bool = False) -> dict:
    """把新确认的产品写入工作区（或用户级）目录，后续模糊检索可直接命中。"""
    if not entry.get("company") or not entry.get("product_name"):
        raise ValueError("catalog 条目至少需要 company 与 product_name")
    entry = dict(entry)
    entry.setdefault("id", make_pid(entry["company"], entry["product_name"]))
    path = user_catalog_path() if user_level or workspace is None else Path(workspace) / "catalog_user.json"
    data = read_json(path, []) or []
    data = [e for e in data if e.get("id") != entry["id"]] + [entry]
    write_json(path, data)
    return entry


# ---------------- 公司别名 ----------------

def company_alias_index(companies: list[dict]) -> list[tuple[str, dict]]:
    pairs = []
    for c in companies:
        for a in {c.get("short", ""), c.get("full", ""), *c.get("aliases", [])}:
            k = name_key(a)
            if len(k) >= 2:
                pairs.append((k, c))
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return pairs


def detect_companies(query: str, companies: list[dict]) -> tuple[list[dict], str]:
    """识别查询中的公司（含“太保”这类集团简称会命中多家子公司），返回公司列表与去掉公司名后的剩余查询。"""
    q = name_key(query)
    hits: list[dict] = []
    for alias, c in company_alias_index(companies):
        if alias and alias in q:
            group_members = [c]
            if c.get("is_group_alias"):
                group_members = [x for x in companies if x.get("group") == c.get("group") and not x.get("is_group_alias")]
            for m in group_members:
                if m not in hits:
                    hits.append(m)
            q = q.replace(alias, " ")
    return hits, q.replace(" ", "")


def company_matches(entry: dict, companies: list[dict]) -> bool:
    ek = {name_key(entry.get("company", "")), name_key(entry.get("insurer_full", ""))}
    for c in companies:
        ck = {name_key(c.get("short", "")), name_key(c.get("full", ""))} | {name_key(a) for a in c.get("aliases", [])}
        if ek & ck:
            return True
    return False


# ---------------- 相似度 ----------------

def _bigrams(s: str) -> list[str]:
    return [s[i:i + 2] for i in range(len(s) - 1)] if len(s) > 1 else [s]


def dice(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    A, B = _bigrams(a), _bigrams(b)
    pool = list(B)
    inter = 0
    for g in A:
        if g in pool:
            pool.remove(g)
            inter += 1
    return 2 * inter / (len(A) + len(B))


def subseq_ratio(q: str, c: str) -> float:
    """查询字符按顺序出现在候选中的比例（近似 LCS，适配“众民保中高端26”这类缩写）。"""
    if not q:
        return 0.0
    blocks = difflib.SequenceMatcher(None, q, c, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks) / len(q)


def containment(q: str, c: str) -> float:
    """查询的二元组被候选包含的比例。"""
    if not q:
        return 0.0
    qb = _bigrams(q)
    cb = set(_bigrams(c))
    return sum(1 for g in qb if g in cb) / len(qb)


def similarity(q: str, c: str) -> float:
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    seq = difflib.SequenceMatcher(None, q, c).ratio()
    cont = containment(q, c)
    sub = subseq_ratio(q, c)
    score = 0.30 * dice(q, c) + 0.20 * seq + 0.30 * cont + 0.20 * sub
    if q in c:
        score = max(score, 0.62 + 0.38 * len(q) / max(len(c), 1))
    return min(score, 1.0)


@dataclass
class Candidate:
    entry: dict
    score: float
    matched_on: str
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        e = self.entry
        return {
            "id": e.get("id"), "score": round(self.score, 3), "company": e.get("company"),
            "product_name": e.get("product_name"), "series": e.get("series"), "version": e.get("version"),
            "product_type": e.get("product_type"), "access_channel": e.get("access_channel"),
            "body_type": e.get("body_type"), "launch": e.get("launch"), "status": e.get("status"),
            "matched_on": self.matched_on, "reasons": self.reasons,
        }


def _version_groups(vs: set[str]) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for v in vs:
        cat = "款别" if v.endswith("款") else ("号数" if v.endswith("号") else "版本号")
        groups.setdefault(cat, set()).add(v)
    return groups


def _names(e: dict) -> Iterable[tuple[str, str]]:
    yield "product_name", e.get("product_name", "")
    for a in e.get("aliases", []) or []:
        yield "alias", a
    if e.get("series"):
        yield "series+name", f"{e.get('series')}{e.get('product_name', '')}"
    for cn in e.get("clause_names", []) or []:
        yield "clause_name", cn


def resolve(query: str, catalog: list[dict], companies: list[dict] | None = None, top_k: int = 8) -> dict:
    """模糊检索产品。返回 {query, companies, candidates, confident, ambiguous, advice}。"""
    companies = companies if companies is not None else load_companies()
    q_companies, q_rest = detect_companies(query, companies)
    q_core = core_key(q_rest) if q_rest else ""
    q_full = name_key(query)
    q_years, q_versions = years_in(query), versions_in(query)
    brands = sorted({name_key(e.get("series") or "") for e in catalog if len(name_key(e.get("series") or "")) >= 2},
                    key=len, reverse=True)
    q_brands = []
    rest = q_full
    for b in brands:
        if b in rest:
            q_brands.append(b)
            rest = rest.replace(b, " ")

    cands: list[Candidate] = []
    for e in catalog:
        best, best_on = 0.0, ""
        for on, nm in _names(e):
            if not nm:
                continue
            s = max(similarity(q_core, core_key(nm)) if q_core else 0.0, similarity(q_full, name_key(nm)) * 0.97)
            if s > best:
                best, best_on = s, on
        reasons = []
        if q_companies:
            if company_matches(e, q_companies):
                best += 0.12
                reasons.append("公司一致")
                if not q_core:
                    best = max(best, 0.5)
            else:
                best -= 0.25
                reasons.append("公司不一致")
        e_text = " ".join(nm for _, nm in _names(e))
        if q_brands:
            e_key = name_key(e_text)
            if any(b in e_key for b in q_brands):
                best += 0.08
                reasons.append("系列一致")
            else:
                best -= 0.25
                reasons.append("系列不一致")
        e_years = years_in(e_text)
        if q_years:
            if q_years & e_years:
                best += 0.10
                reasons.append("年份一致")
            elif e_years:
                best -= 0.22
                reasons.append(f"年份不同（候选{'/'.join(sorted(e_years))}）")
            else:
                best -= 0.05
                reasons.append("候选名称未标年份")
        if q_versions:
            e_versions = versions_in(e_text)
            for cat_name, qv in _version_groups(q_versions).items():
                ev = _version_groups(e_versions).get(cat_name, set())
                if not ev:
                    continue
                if qv & ev:
                    best += 0.06
                    reasons.append(f"{cat_name}一致")
                else:
                    best -= 0.18
                    reasons.append(f"{cat_name}不同（候选{'/'.join(sorted(ev))}）")
        if best > 0.2:
            cands.append(Candidate(e, max(0.0, min(best, 1.0)), best_on, reasons))

    cands.sort(key=lambda c: (-c.score, str(c.entry.get("launch") or "")), reverse=False)
    top = cands[:top_k]
    s1 = top[0].score if top else 0.0
    s2 = top[1].score if len(top) > 1 else 0.0
    confident = bool(top) and s1 >= 0.72 and (s1 - s2) >= 0.06
    ambiguous = bool(top) and not confident and s1 >= 0.45
    if not top or s1 < 0.45:
        advice = "目录未可靠命中：请用 `queries` 生成检索词进行联网检索，确认官方产品名后用 `catalog-add` 加入目录。"
    elif confident:
        advice = "首选候选可信；仍须在归档时用条款名称/备案号核对版本。"
    else:
        advice = "存在多个相近候选（常见于同系列不同年份/计划）：请向用户确认，或把同系列各版本都纳入版本对比。"
    series_members = []
    if top and top[0].entry.get("series"):
        sk = name_key(top[0].entry["series"])
        series_members = [
            {"id": e.get("id"), "product_name": e.get("product_name"), "launch": e.get("launch"), "status": e.get("status")}
            for e in catalog
            if name_key(e.get("series") or "") == sk and name_key(e.get("company") or "") == name_key(top[0].entry.get("company") or "")
        ]
        series_members.sort(key=lambda x: str(x.get("launch") or ""))
    return {
        "query": query,
        "detected_companies": [c.get("short") for c in q_companies],
        "candidates": [c.to_dict() for c in top],
        "confident": confident,
        "ambiguous": ambiguous,
        "same_series": series_members,
        "advice": advice,
    }


def get_entry(pid_or_name: str, catalog: list[dict]) -> dict | None:
    for e in catalog:
        if e.get("id") == pid_or_name:
            return e
    k = name_key(pid_or_name)
    for e in catalog:
        if name_key(e.get("product_name", "")) == k:
            return e
    return None


def company_info(name: str, companies: list[dict] | None = None) -> dict | None:
    companies = companies if companies is not None else load_companies()
    k = name_key(name)
    for c in companies:
        keys = {name_key(c.get("short", "")), name_key(c.get("full", ""))} | {name_key(a) for a in c.get("aliases", [])}
        if k in keys and not c.get("is_group_alias"):
            return c
    for c in companies:
        if not c.get("is_group_alias") and (k and (k in name_key(c.get("full", "")) or name_key(c.get("short", "")) in k)):
            return c
    return None
