"""原文引文核验：确认每条引文确实出现在已归档资料的抽取文本中，并定位页码。

比对前统一全半角、去掉空白与零宽字符（PDF 抽取常在中英文间插空格、句中断行）。
- exact：规范化后逐字出现
- fuzzy：PDF 在句中插入脚注编号、页眉等导致不能逐字匹配，但相似度 ≥ 0.90（标注相似度，需人工留意）
- not_found：找不到，视为无效证据
引文中可用“……”或“...”连接同一页（或相邻页）内的多段原文。
"""
from __future__ import annotations

import difflib
import re
from functools import lru_cache
from pathlib import Path

from .common import read_text
from .normalize import match_key
from .textextract import text_to_pages

FUZZY_OK = 0.90
_SPLIT = re.compile(r"(?:……|\.\.\.|…)+")


@lru_cache(maxsize=256)
def _load_pages(path: str, mtime: float) -> tuple[tuple[str, ...], tuple[str, ...]]:
    pages = text_to_pages(read_text(Path(path)))
    return tuple(pages), tuple(match_key(p) for p in pages)


def load_pages(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    path = Path(path)
    return _load_pages(str(path), path.stat().st_mtime)


def _fuzzy_find(q: str, hay: str) -> tuple[float, int]:
    """在 hay 中找与 q 最相似的窗口，返回 (相似度, 起点)。用 8 字锚点缩小范围。"""
    if not q or not hay:
        return 0.0, -1
    n = len(q)
    anchors = {q[i:i + 8] for i in range(0, max(1, n - 7), max(1, n // 6))}
    starts = set()
    for a in anchors:
        start = 0
        while True:
            j = hay.find(a, start)
            if j < 0:
                break
            off = q.find(a)
            starts.add(max(0, j - off))
            start = j + 1
            if len(starts) > 200:
                break
    best, best_s = 0.0, -1
    for s in starts:
        for width in (n, int(n * 1.1) + 2, int(n * 1.25) + 4):
            window = hay[s:s + width]
            r = difflib.SequenceMatcher(None, q, window, autojunk=False).ratio()
            if r > best:
                best, best_s = r, s
    return best, best_s


def locate(quote: str, pages: tuple[str, ...], keys: tuple[str, ...], page_hint: int | None = None) -> dict:
    """在按页文本中定位引文，返回 {match, page, score}。"""
    segs = [match_key(s) for s in _SPLIT.split(quote or "") if match_key(s)]
    if not segs:
        return {"match": "empty", "page": None, "score": 0.0}
    order = list(range(len(keys)))
    if page_hint and 1 <= page_hint <= len(keys):
        order.sort(key=lambda i: abs(i - (page_hint - 1)))
    def in_order(hay: str) -> bool:
        pos = 0
        for s in segs:
            j = hay.find(s, pos)
            if j < 0:
                return False
            pos = j + len(s)
        return True

    # 1) 逐字匹配：先找单页，再找相邻两页（引文跨页或“……”连接的片段分处两页）
    for i in order:
        if in_order(keys[i]):
            return {"match": "exact", "page": i + 1, "score": 1.0}
    for i in order:
        if i + 1 < len(keys) and in_order(keys[i] + keys[i + 1]):
            return {"match": "exact", "page": i + 1, "score": 1.0}
    # 2) 跨页拼接：引文跨越分页处
    joined = "".join(keys)
    if len(segs) == 1 and segs[0] in joined:
        idx = joined.find(segs[0])
        acc = 0
        for i, k in enumerate(keys):
            if acc + len(k) > idx:
                return {"match": "exact", "page": i + 1, "score": 1.0}
            acc += len(k)
    # 3) 近似匹配（逐片段取最低分；先单页，单页不够再试相邻两页拼接）
    best_page, scores = None, []
    for s in segs:
        top, top_page = 0.0, None
        for i in order:
            r, _ = _fuzzy_find(s, keys[i])
            if r > top:
                top, top_page = r, i + 1
            if top >= 0.995:
                break
        if top < FUZZY_OK:
            for i in order:
                if i + 1 < len(keys):
                    r, _ = _fuzzy_find(s, keys[i] + keys[i + 1])
                    if r > top:
                        top, top_page = r, i + 1
        scores.append(top)
        best_page = best_page or top_page
    score = min(scores) if scores else 0.0
    if score >= FUZZY_OK:
        return {"match": "fuzzy", "page": best_page, "score": round(score, 3)}
    return {"match": "not_found", "page": best_page, "score": round(score, 3)}


def verify_quote(quote: str, text_path: Path, page_hint: int | None = None) -> dict:
    pages, keys = load_pages(text_path)
    return locate(quote, pages, keys, page_hint)


def context(text_path: Path, page: int, quote: str, width: int = 60) -> str:
    """返回引文所在页的原样上下文（用于报告/Excel 显示）。"""
    pages, _ = load_pages(text_path)
    if not page or page > len(pages):
        return ""
    raw = re.sub(r"\s+", "", pages[page - 1])
    q = re.sub(r"\s+", "", quote)[:12]
    i = raw.find(q)
    if i < 0:
        return ""
    return raw[max(0, i - width): i + len(quote) + width]
