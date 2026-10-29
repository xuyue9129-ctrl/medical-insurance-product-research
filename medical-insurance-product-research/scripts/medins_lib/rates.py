"""费率表解析：从抽取文本中识别“年龄档 + 一串数字”的行，按上下文分块。

脚本只做“逐行搬运”，不猜列含义：每块保留前面的标题/表头行（如“有基本医疗保险”“交费方式：年交”“计划一 计划二”），
由研究者确认列含义后用 `rates import` 写入记录；每一行都以原文行作为引文，可逐字核验。
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import read_text
from .textextract import text_to_pages

_ROW = re.compile(
    r"^\s*(?P<age>(?:出生满?\s*)?\d{1,3}\s*(?:天|日|周岁|岁)?\s*(?:[-–—~～至]\s*\d{1,3}\s*(?:周岁|岁)?)?)"
    r"\s+(?P<nums>(?:[\d,]+(?:\.\d+)?\s+){0,30}[\d,]+(?:\.\d+)?)\s*$")


def _parse_row(line: str) -> dict | None:
    m = _ROW.match(line)
    if not m:
        return None
    age = re.sub(r"\s+", "", m.group("age"))
    nums = m.group("nums").split()
    if not nums:
        return None
    lo = re.match(r"(?:出生满?)?(\d{1,3})", age)
    if not lo or int(lo.group(1)) > 120:
        return None
    return {"age": age, "values": nums, "line": line.strip()}


def age_range(age: str) -> tuple[int, int] | None:
    nums = [int(x) for x in re.findall(r"\d{1,3}", age)]
    if not nums:
        return None
    if re.search(r"天|日", age) and len(nums) >= 2:
        return 0, nums[-1]
    return (nums[0], nums[-1]) if len(nums) > 1 else (nums[0], nums[0])


def parse_rate_text(pages: list[str], max_context: int = 6) -> list[dict]:
    """返回费率块：[{block, page, context, rows:[{age, values, line}], columns_guess}]"""
    blocks: list[dict] = []
    for pno, page in enumerate(pages, 1):
        lines = [ln for ln in page.splitlines() if ln.strip()]
        ctx: list[str] = []
        cur = None
        for ln in lines:
            row = _parse_row(ln)
            if row:
                row["page"] = pno
                if cur is None:
                    cur = {"page": pno, "context": ctx[-max_context:], "rows": []}
                    blocks.append(cur)
                cur["rows"].append(row)
            else:
                cur = None
                ctx.append(ln.strip())
    # 跨页续表：下一页开头没有新表头、年龄档紧接上一块且列数相同，则并入上一块
    merged: list[dict] = []
    head_kw = re.compile(r"有基本|无基本|计划|方案|交费|年交|月交|费率表|等待期|责任")
    for b in blocks:
        if merged:
            prev = merged[-1]
            pr, nr = age_range(prev["rows"][-1]["age"]), age_range(b["rows"][0]["age"])
            same_cols = len(prev["rows"][-1]["values"]) == len(b["rows"][0]["values"])
            if (b["page"] == prev["rows"][-1]["page"] + 1 and same_cols and pr and nr and nr[0] == pr[1] + 1
                    and not any(head_kw.search(c) for c in b["context"])):
                prev["rows"].extend(b["rows"])
                prev["continued_pages"] = sorted(set(prev.get("continued_pages", [prev["page"]]) + [b["page"]]))
                continue
        merged.append(b)
    blocks = merged
    out = []
    for i, b in enumerate(blocks, 1):
        if len(b["rows"]) < 2:
            continue
        widths = {len(r["values"]) for r in b["rows"]}
        header = next((c for c in reversed(b["context"]) if re.search(r"计划|方案|档|免赔|版|男|女|年交|月交", c)), "")
        b.update({"block": len(out) + 1, "n_cols": max(widths), "uniform": len(widths) == 1,
                  "columns_guess": header.split() if header else []})
        out.append(b)
    return out


def parse_rate_file(text_path: Path) -> list[dict]:
    return parse_rate_text(text_to_pages(read_text(Path(text_path))))


def pick_age(blocks: list[dict], age: int = 50) -> list[dict]:
    """找出各块中覆盖目标年龄的行（默认50岁，即46–50岁档），作为“50岁保费”的候选。"""
    out = []
    for b in blocks:
        for r in b["rows"]:
            rg = age_range(r["age"])
            if rg and rg[0] <= age <= rg[1]:
                out.append({"block": b["block"], "page": r.get("page", b["page"]), "context": b["context"][-4:], "age": r["age"],
                            "values": r["values"], "line": r["line"], "columns_guess": b.get("columns_guess", [])})
                break
    return out


def block_to_table(block: dict, columns: list[str], col_index: list[int] | None, basis: str, doc: str, unit: str = "元") -> dict:
    """把一个费率块按研究者确认的列含义转成记录用的 rate_table，并为每行生成原文引文。"""
    idx = col_index or list(range(len(columns)))
    if len(idx) != len(columns):
        raise ValueError("columns 与 col_index 数量不一致")
    rows, evidence = [], []
    for r in block["rows"]:
        vals = [r["values"][i] if i < len(r["values"]) else "" for i in idx]
        rows.append({"age": r["age"], "values": vals})
        evidence.append({"doc": doc, "page": r.get("page", block["page"]), "quote": r["line"]})
    ctx = " / ".join(block.get("context", [])[-4:])
    return {"basis": basis, "unit": unit, "columns": columns, "rows": rows, "source_context": ctx,
            "source_block": block["block"], "evidence": evidence}
