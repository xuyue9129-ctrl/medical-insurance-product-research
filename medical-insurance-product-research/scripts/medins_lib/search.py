"""检索：生成检索词、（可选）内置网页检索、从页面中发现资料链接。

推荐做法：由 Agent 用自身的联网搜索/浏览工具执行 `queries` 生成的检索词；
没有联网工具时，可用 `search` 子命令走内置的 Bing/DuckDuckGo 网页检索（尽力而为，可能被限流）。
拿到官方产品页/信息披露页后，用 `discover` 列出页面上的条款、费率表等附件链接，再 `fetch` 下载归档。
"""
from __future__ import annotations

import html as _html
import json
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import quote_plus, unquote, urljoin, urlparse

from .catalog import company_info, similarity
from .common import read_text
from .doctypes import classify_name, filename_from_url, type_name
from .fetch import http_get
from .normalize import core_key, nfkc

DOC_QUERY_TERMS = [
    ("01", "条款"), ("02", "费率表"), ("03", "健康告知"), ("05", "投保须知"), ("05", "产品说明书"),
    ("04", "责任免除 严重既往症 清单"), ("06", "医院清单 特药清单"), ("07", "健康管理服务手册"),
]

DISCLOSURE_HINTS = [
    "保险公司官网 →“公开信息披露”→“互联网保险信息披露 / 产品基本信息”→ 按产品名或备案条款名查找条款、费率表、产品说明书",
    "人身险公司（寿险/健康险）产品：中国保险行业协会“人身保险产品信息库”（https://rsxcpk.iachina.cn/，需滑块验证，适合人工核对备案条款名/状态）",
    "财产险公司产品：国家金融监督管理总局“财产险公司备案产品目录查询”与保险行业协会“财产保险公司自主注册产品查询”（https://cxcx.iachina.cn/，需登录）",
    "持牌中介销售页（慧择、开心保、小雨伞、蚂蚁保等）常附正式条款/费率/投保须知 PDF，可作 A 级来源，但须核对版本",
    "健康告知多在动态投保页展示：用浏览器打开投保流程逐字抄录或截图，并以 `gap` 登记“动态投保页核验说明（非逐字原件）”",
]


def build_queries(product_name: str, company: str = "", clause_names: list[str] | None = None) -> dict:
    """为一款产品生成分门别类的检索词。"""
    c = company_info(company) if company else None
    full = c.get("full") if c else ""
    domains = (c.get("domains") if c else []) or []
    short = c.get("short") if c else company
    name = nfkc(product_name).strip()
    queries = []
    for code, term in DOC_QUERY_TERMS:
        queries.append({"doc_type": code, "purpose": f"找{type_name(code)}", "query": f"{short} {name} {term}".strip()})
    for d in domains[:3]:
        queries.append({"doc_type": "01", "purpose": "官网站内检索", "query": f"site:{d} {name}"})
        queries.append({"doc_type": "02", "purpose": "官网站内检索（PDF）", "query": f"site:{d} {name} 条款 费率 filetype:pdf"})
    for cn in clause_names or []:
        queries.append({"doc_type": "01", "purpose": "按备案条款名检索", "query": f"\"{cn}\" 条款"})
        queries.append({"doc_type": "02", "purpose": "按备案条款名检索", "query": f"\"{cn}\" 费率表"})
    if full:
        queries.append({"doc_type": "05", "purpose": "公开信息披露入口", "query": f"{full} 互联网保险 信息披露 {name}"})
    queries.append({"doc_type": "08", "purpose": "上市时间/发布稿（仅作线索）", "query": f"{short} {name} 上市 发布"})
    queries.append({"doc_type": "08", "purpose": "版本演进线索", "query": f"{short} {name} 升级 新版 对比"})
    return {"product_name": name, "company": short, "insurer_full": full, "official_domains": domains,
            "queries": queries, "disclosure_hints": DISCLOSURE_HINTS}


# ---------------- 内置网页检索（兜底） ----------------

def _get_html(url: str, insecure: bool = False) -> str:
    tmp = Path(tempfile.mkdtemp(prefix="medins_s_")) / "page.html"
    try:
        http_get(url, tmp, retries=2, timeout=20, insecure=insecure)
        raw = tmp.read_bytes()
    finally:
        try:
            tmp.unlink()
            tmp.parent.rmdir()
        except OSError:
            pass
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_tags(s: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def web_search(query: str, engine: str = "bing", limit: int = 10) -> list[dict]:
    """内置检索（解析搜索引擎 HTML 结果页），失败返回空列表而不抛异常。"""
    results: list[dict] = []
    try:
        if engine == "bing":
            page = _get_html(f"https://cn.bing.com/search?q={quote_plus(query)}&setlang=zh-CN")
            for m in re.finditer(r'<li class="b_algo".*?<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)</li>', page, re.S):
                snippet = re.search(r"<p[^>]*>(.*?)</p>", m.group(3), re.S)
                results.append({"title": _strip_tags(m.group(2)), "url": _html.unescape(m.group(1)),
                                "snippet": _strip_tags(snippet.group(1)) if snippet else "", "engine": "bing"})
        elif engine == "duckduckgo":
            page = _get_html(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
            for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
                u = _html.unescape(m.group(1))
                mm = re.search(r"uddg=([^&]+)", u)
                results.append({"title": _strip_tags(m.group(2)), "url": unquote(mm.group(1)) if mm else u,
                                "snippet": "", "engine": "duckduckgo"})
    except Exception as e:  # noqa: BLE001 - 检索是尽力而为
        return [{"error": f"{engine} 检索失败：{e}"}]
    return results[:limit]


def search_all(product_name: str, company: str = "", engines=("bing", "duckduckgo"), per_query: int = 6,
               max_queries: int = 8, pause: float = 1.0) -> list[dict]:
    q = build_queries(product_name, company)
    seen, out = set(), []
    for item in q["queries"][:max_queries]:
        for eng in engines:
            for r in web_search(item["query"], eng, per_query):
                if "error" in r:
                    out.append({**r, "query": item["query"]})
                    continue
                if r["url"] in seen:
                    continue
                seen.add(r["url"])
                code = classify_name(r.get("title", ""), filename_from_url(r["url"]))
                out.append({**r, "query": item["query"], "doc_type_guess": code or "", "is_pdf": r["url"].lower().split("?")[0].endswith(".pdf")})
            time.sleep(pause)
    return out


# ---------------- 页面附件发现 ----------------
_DOC_EXT = re.compile(r"\.(pdf|docx?|xlsx?|html?)(?:$|\?)", re.I)
_RAW_URL = re.compile(r"""https?://[^\s"'<>\\]+?\.(?:pdf|docx?|xlsx?)(?:\?[^\s"'<>\\]*)?""", re.I)


def discover_links(source: str, product_name: str = "", insecure: bool = False, base_url: str = "") -> list[dict]:
    """从网页（URL 或本地保存的 HTML 文件）中找出条款/费率表/清单等附件链接，并按相关度排序。"""
    if re.match(r"^https?://", source):
        page, base = _get_html(source, insecure=insecure), source
    else:
        page, base = read_text(Path(source)), base_url
    found: dict[str, dict] = {}

    def add(url: str, anchor: str, how: str) -> None:
        url = _html.unescape(url.strip())
        if not url or url.startswith(("javascript:", "mailto:", "#")):
            return
        full = urljoin(base, url) if base else url
        if not re.match(r"^https?://", full):
            return
        anchor = re.sub(r"\s+", " ", _strip_tags(anchor))[:120]
        rec = found.get(full)
        if rec and len(rec["anchor"]) >= len(anchor):
            return
        name = filename_from_url(full)
        code = classify_name(anchor, name)
        is_doc = bool(_DOC_EXT.search(urlparse(full).path)) or bool(code)
        if not is_doc:
            return
        rel = similarity(core_key(product_name), core_key(anchor + name)) if product_name else 0.0
        found[full] = {"url": full, "anchor": anchor, "file_name": name, "doc_type_guess": code or "",
                       "doc_type_name": type_name(code) if code else "", "relevance": round(rel, 3), "found_by": how}

    for m in re.finditer(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", page, re.S | re.I):
        add(m.group(1), m.group(2), "a")
    for m in re.finditer(r"<(?:iframe|embed|object)\b[^>]*?(?:src|data)\s*=\s*[\"']([^\"']+)[\"']", page, re.I):
        add(m.group(1), "嵌入文档", "embed")
    for m in _RAW_URL.finditer(page.replace("\\/", "/")):
        add(m.group(0), "", "raw")
    for m in re.finditer(r'"(?:name|title|fileName)"\s*:\s*"([^"]{2,80})"[^{}]{0,300}?"(?:url|fileUrl|link|path)"\s*:\s*"([^"]+)"', page):
        add(m.group(2).replace("\\/", "/"), m.group(1), "json")
    out = sorted(found.values(), key=lambda x: (x["doc_type_guess"] == "", -x["relevance"], x["doc_type_guess"]))
    return out


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
