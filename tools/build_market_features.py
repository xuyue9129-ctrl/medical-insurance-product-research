"""从本机样本摘要目录生成公开的“市场样本特征表” assets/catalog/market_features.json。

用法：python tools/build_market_features.py [--from ~/.medins-research/catalog.json] [--exclude 关键词 ...]

只输出：公司、产品名、上市月份、各创新特征“有(1)/无(0)”（摘要中没有可比内容的特征不写）。
不输出摘要原文、具体数值或研究判断。特征按 assets/schema/features.json 的规则从摘要中判定。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "medical-insurance-product-research" / "scripts"))

from medins_lib.common import read_json, write_json  # noqa: E402
from medins_lib.innovation import _has, load_features  # noqa: E402

OUT = ROOT / "medical-insurance-product-research" / "assets" / "catalog" / "market_features.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="src", default=str(Path.home() / ".medins-research" / "catalog.json"))
    ap.add_argument("--exclude", action="append", default=[], help="产品名包含该关键词则不公开（可多次）")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    feats = load_features()
    rows, as_of = [], ""
    for e in read_json(Path(args.src), []) or []:
        ov = e.get("overview")
        if not ov or any(x in e["product_name"] for x in args.exclude):
            continue
        tags = {}
        for f in feats:
            texts = [ov.get(k, "") for k in f.get("overview", [])]
            if any(texts):
                tags[f["key"]] = 1 if _has(f, texts)[0] else 0
        m = re.search(r"20\d\d(?:-\d{2})?", e.get("launch") or ov.get("launch_date", "") or "")
        rows.append({"company": e["company"], "product_name": e["product_name"], "launch": m.group(0) if m else "", "features": tags})
        as_of = max(as_of, e.get("overview_as_of", "") or "")
    rows.sort(key=lambda r: (r["company"], r["product_name"]))
    write_json(Path(args.out), {
        "schema": "medins-market-features/1", "as_of": as_of,
        "_说明": "市场样本特征表：各产品是否具备 features.json 中的特征（1=有，0=无；未列出=样本中无可比内容）。"
                 "由公开条款整理的样本摘要按特征规则判定，用作创新点识别的起始对照样本；判断是否创新仍须全网核验（market）。"
                 "已知局限：摘要只记录既往症“特殊可赔/不赔”，“一般既往症可赔”等特征可能被低估。",
        "products": rows})
    print(f"已写入 {args.out}：{len(rows)} 款产品")
    return 0


if __name__ == "__main__":
    sys.exit(main())
