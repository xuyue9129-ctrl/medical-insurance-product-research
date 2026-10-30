#!/usr/bin/env python3
"""医疗险产品研究 Skill 命令行入口（跨平台，Python 3.8+）。

常用流程：
  python medins.py doctor
  python medins.py init -w <工作区>
  python medins.py resolve "蓝医保2027"
  python medins.py register -w <工作区> --from-catalog <id>
  python medins.py queries "<产品名>" --company <公司>        # 交给 Agent 的联网搜索执行
  python medins.py discover <官网产品页URL> --product "<产品名>"
  python medins.py fetch -w <工作区> --pid <pid> <PDF链接>...
  python medins.py import -w <工作区> --pid <pid> <本地文件或目录>
  python medins.py extract -w <工作区> --apply-safe
  python medins.py text -w <工作区> --pid <pid> --grep 免赔额
  python medins.py record apply -w <工作区> --pid <pid> --file patch.json
  python medins.py build -w <工作区>                          # 校验→对比→创新点→Excel 对比表
  python medins.py deliver -w <工作区> --report yes --ppt yes  # 登记用户是否需要报告/PPT
  python medins.py report -w <工作区> / ppt -w <工作区>        # 分析报告（Word）/ 汇报 PPT
每个子命令加 -h 查看参数；加 --json 输出机器可读结果。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from medins_lib.common import MedinsError, setup_utf8  # noqa: E402


def _ws(args):
    from medins_lib.workspace import Workspace
    root = args.workspace or os.environ.get("MEDINS_WORKSPACE") or "medins_workspace"
    return Workspace(root)


def _out(args, data, human=None):
    if getattr(args, "json", False) or human is None:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        human(data)


def _pids(ws, args) -> list[str]:
    vals = getattr(args, "pid", None) or getattr(args, "pids", None)
    if not vals:
        return list(ws.products())
    if isinstance(vals, str):
        vals = [vals]
    return [ws.get(v)["pid"] for v in vals]


# ---------------- 子命令 ----------------

def cmd_doctor(args):
    import platform
    from medins_lib.textextract import available_pdf_backends
    checks = {"python": sys.version.split()[0], "platform": platform.platform()}
    ok = sys.version_info >= (3, 8)
    libs = {}
    for mod, pipname, need in (("openpyxl", "openpyxl", True), ("requests", "requests", False), ("bs4", "beautifulsoup4", False),
                               ("docx", "python-docx", False), ("pdfplumber", "pdfplumber", False), ("fitz", "pymupdf", False),
                               ("pypdf", "pypdf", False), ("pptx", "python-pptx", False)):
        try:
            m = __import__(mod)
            libs[pipname] = getattr(m, "__version__", "ok")
        except Exception:
            libs[pipname] = None
            if need:
                ok = False
    backends = available_pdf_backends()
    if not backends:
        ok = False
    checks.update({"libraries": libs, "pdf_backends": backends})
    advice = []
    if not backends:
        advice.append("安装任一 PDF 解析库：pip install pymupdf（推荐）或 pdfplumber 或 pypdf")
    if not libs.get("openpyxl"):
        advice.append("安装 openpyxl：pip install openpyxl（生成 Excel 必需）")
    if not libs.get("python-docx"):
        advice.append("可选：pip install python-docx（输出 Word 报告）")
    if not libs.get("python-pptx"):
        advice.append("可选：pip install python-pptx（输出汇报 PPT）")
    if not libs.get("pdfplumber"):
        advice.append("可选：pip install pdfplumber（费率表表格抽取更稳）")
    from medins_lib.catalog import load_catalog
    checks["catalog_products"] = len(load_catalog())
    checks["ok"] = ok
    checks["advice"] = advice

    def human(d):
        print(f"Python {d['python']} | {d['platform']}")
        for k, v in d["libraries"].items():
            print(f"  {'✔' if v else '✘'} {k} {v or ''}")
        print(f"  PDF 解析后端：{', '.join(d['pdf_backends']) or '无'}；内置目录 {d['catalog_products']} 款")
        for a in d["advice"]:
            print("  → " + a)
        print("环境可用" if d["ok"] else "环境缺少必需组件")
    _out(args, checks, human)
    return 0 if ok else 2


def cmd_init(args):
    ws = _ws(args)
    cfg = ws.init(args.title or "")
    _out(args, {"workspace": str(ws.root), "title": cfg.get("title")}, lambda d: print(f"工作区已就绪：{d['workspace']}"))


def cmd_resolve(args):
    from medins_lib.catalog import load_catalog, resolve
    wsroot = args.workspace or os.environ.get("MEDINS_WORKSPACE")
    res = resolve(args.query, load_catalog(Path(wsroot) if wsroot else None), top_k=args.top)

    def human(d):
        print(f"查询：{d['query']}  识别公司：{'、'.join(d['detected_companies']) or '无'}")
        for i, c in enumerate(d["candidates"], 1):
            print(f" {i}. [{c['score']:.2f}] {c['company']}·{c['product_name']}  ({c['access_channel'] or '准入待核'}；{c['launch'] or '上市待核'}；{c['status'] or ''})  id={c['id']}"
                  + (f"  {'/'.join(c['reasons'])}" if c["reasons"] else ""))
        if d["same_series"]:
            print("同系列：" + "；".join(f"{x['product_name']}({x['launch'] or '?'})" for x in d["same_series"]))
        print(("✔ 可信" if d["confident"] else ("⚠ 有歧义" if d["ambiguous"] else "✘ 未命中")) + "：" + d["advice"])
    _out(args, res, human)


def cmd_queries(args):
    from medins_lib.search import build_queries
    res = build_queries(args.product, args.company or "", args.clause_name or [])

    def human(d):
        print(f"{d['company']}·{d['product_name']}  官网域名：{', '.join(d['official_domains']) or '未登记'}")
        for q in d["queries"]:
            print(f"  [{q['doc_type']}] {q['purpose']}：{q['query']}")
        print("资料入口提示：")
        for h in d["disclosure_hints"]:
            print("  - " + h)
    _out(args, res, human)


def cmd_search(args):
    from medins_lib.search import search_all
    res = search_all(args.product, args.company or "", engines=tuple(args.engines.split(",")), max_queries=args.max_queries)
    _out(args, res, lambda d: [print(f"[{r.get('doc_type_guess') or '?'}] {r.get('title', '')} {r.get('url', r.get('error', ''))}") for r in d])


def cmd_discover(args):
    from medins_lib.search import discover_links
    res = discover_links(args.source, args.product or "", insecure=args.insecure, base_url=args.base_url or "")
    if args.save:
        Path(args.save).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    _out(args, res, lambda d: [print(f"[{r['doc_type_guess'] or '??'} {r['relevance']:.2f}] {r['anchor'] or r['file_name']}  {r['url']}") for r in d]
         or print(f"共 {len(d)} 个附件链接" + (f"，已保存 {args.save}" if args.save else "")))


def cmd_register(args):
    from medins_lib.catalog import company_info, get_entry, load_catalog
    ws = _ws(args)
    ws.load()
    meta = {}
    if args.pid:  # 更新已登记产品（如补登备案条款名）
        meta = dict(ws.get(args.pid))
    if args.from_catalog:
        e = get_entry(args.from_catalog, load_catalog(ws.root))
        if not e:
            raise MedinsError(f"目录中没有：{args.from_catalog}（先 resolve 获取 id）")
        meta = {"company": e["company"], "product_name": e["product_name"], "insurer_full": e.get("insurer_full", ""),
                "series": e.get("series", ""), "series_line": e.get("series_line", ""), "access_channel": e.get("access_channel", ""),
                "body_type": e.get("body_type", ""), "launch": e.get("launch", ""), "catalog_id": e["id"],
                "clause_names": e.get("clause_names", [])}
    for k, v in (("company", args.company), ("product_name", args.product), ("series", args.series), ("version", args.version),
                 ("access_channel", args.access), ("body_type", args.body_type), ("launch", args.launch), ("insurer_full", args.insurer_full)):
        if v:
            meta[k] = v
    if args.role:
        meta["role"] = "peer" if args.role == "peer" else ""
    if args.clause_name:
        meta["clause_names"] = list(dict.fromkeys((meta.get("clause_names") or []) + args.clause_name))
    if meta.get("company") and not meta.get("insurer_full"):
        c = company_info(meta["company"])
        if c:
            meta["insurer_full"] = c["full"]
            meta["company"] = c["short"]
    p = ws.register(meta)
    if args.add_to_catalog:
        from medins_lib.catalog import add_to_catalog
        entry = {k: p.get(k) for k in ("company", "insurer_full", "product_name", "series", "access_channel", "body_type",
                                       "launch", "clause_names")}
        entry["origin"] = "workspace"
        add_to_catalog(entry, ws.root)
    _out(args, p, lambda d: print(f"已登记 {d['pid']}：{d['company']}·{d['product_name']}{'（同业对照）' if d.get('role') == 'peer' else ''}"
                                  f"\n  归档目录：{ws.root / d['archive_dir']}"
                                  + (f"\n  备案条款：{'；'.join(d.get('clause_names') or [])}" if d.get("clause_names") else "")))


def cmd_list(args):
    ws = _ws(args)
    prods = ws.products()

    def human(d):
        for pid, p in d.items():
            print(f"{pid}  {p['company']}·{p['product_name']}{'  [同业对照]' if p.get('role') == 'peer' else ''}  系列={p.get('series') or '—'}  "
                  f"条款={'；'.join(p.get('clause_names') or []) or '未登记'}")
    _out(args, prods, human)


def _print_doc(d):
    if d.get("error"):
        print(f"  ✘ {d.get('file')}：{d['error']}")
        return
    if d.get("gap"):
        print(f"  ○ {d['file']} → 登记为资料说明：{d['gap']['doc_type']} {d['gap']['status']}")
        return
    vc = d.get("version_check", {})
    print(f"  {'↺ 重复' if d.get('duplicate') else '✔'} {d['file']}  [{d['doc_type_name']}·{d.get('classified_by', '')}] 证据{d.get('evidence_level')} "
          f"{(str(d.get('pages')) + '页') if d.get('pages') else ''} 版本核对={vc.get('status')}"
          + (f"\n     ⚠ {d['warning']}" if d.get("warning") else "")
          + (f"\n     识别到备案条款名：{'；'.join(d['detected_clause_names'])}" if d.get("detected_clause_names") else ""))


def _clause_hint(ws, pid, res, args):
    if args.json:
        return
    found = []
    for d in res:
        for cn in d.get("detected_clause_names") or []:
            if cn not in found:
                found.append(cn)
    have = ws.get(pid).get("clause_names") or []
    new = [c for c in found if c not in have]
    if new:
        print("\n提示：条款中识别到备案条款名。确认属于本产品/本版本后登记，以便核对费率表、清单等文件的版本：")
        print(f"  python medins.py register -w \"{ws.root}\" --pid {pid} " + " ".join(f"--clause-name \"{c}\"" for c in new))
        print(f"  python medins.py verify-docs -w \"{ws.root}\" --pid {pid}")


def cmd_fetch(args):
    from medins_lib.fetch import fetch_url
    ws = _ws(args)
    pid = ws.get(args.pid)["pid"]
    targets = [(u, "", args.type) for u in args.urls]
    if args.from_discover:
        types = set((args.types or "").split(",")) - {""}
        for it in json.loads(Path(args.from_discover).read_text(encoding="utf-8")):
            if not types or it.get("doc_type_guess") in types:
                targets.append((it["url"], it.get("anchor", ""), args.type))
    res = []
    for url, anchor, t in targets:
        try:
            res.append(fetch_url(ws, pid, url, anchor=anchor, doc_type=t, title=args.title, referer=args.referer,
                                 evidence_level=args.evidence_level, insecure=args.insecure, note=args.note or ""))
        except MedinsError as e:
            res.append({"file": url, "error": str(e)})
    _out(args, res, lambda d: [_print_doc(x) for x in d])
    _clause_hint(ws, pid, res, args)
    return 1 if any(x.get("error") for x in res) else 0


def cmd_import(args):
    from medins_lib.fetch import archive_file, import_folder
    ws = _ws(args)
    pid = ws.get(args.pid)["pid"]
    res = []
    for p in args.paths:
        path = Path(p)
        if path.is_dir():
            res += import_folder(ws, pid, path, evidence_level=args.evidence_level)
        else:
            src = {"via": "local-import", "local_path": str(path)}
            if args.source_url:
                src["url"] = args.source_url
            try:
                res.append(archive_file(ws, pid, path, source=src, doc_type=args.type, title=args.title, evidence_level=args.evidence_level))
            except MedinsError as e:
                res.append({"file": str(path), "error": str(e)})
    _out(args, res, lambda d: [_print_doc(x) for x in d])
    _clause_hint(ws, pid, res, args)


def cmd_gap(args):
    from medins_lib.fetch import mark_gap
    ws = _ws(args)
    g = mark_gap(ws, args.pid, args.type, args.status, args.note or "", args.evidence or "")
    _out(args, g, lambda d: print(f"已登记：{d['doc_type']} {d['doc_type_name']} — {d['status']}（{d['file']}）"))


def cmd_verify_docs(args):
    from medins_lib.fetch import reverify_documents
    ws = _ws(args)
    out = {pid: reverify_documents(ws, pid) for pid in _pids(ws, args)}
    _out(args, out, lambda d: [print(f"{pid}: " + "；".join(f"{x['file']}={x['version_check']}" for x in lst)) for pid, lst in d.items()])


def cmd_extract(args):
    from medins_lib.extract import run_extract
    ws = _ws(args)
    out = [run_extract(ws, pid, apply_safe=args.apply_safe) for pid in _pids(ws, args)]

    def human(d):
        for r in d:
            print(f"{r['pid']}: 草稿字段 {len(r['draft_fields'])} 个；样本摘要提示 {'有' if r['hints'] else '无'}")
            for a in r["applied"]:
                print(f"   {'✔' if a['applied'] else '✘'} {a['field']} {a.get('status', '')} {'；'.join(a['problems'] + a['warnings'])}")
    _out(args, out, human)


def cmd_drafts(args):
    from medins_lib.records import load_record, resolve_field
    ws = _ws(args)
    rec = load_record(ws, args.pid)
    drafts = rec.get("drafts", {})
    if args.field:
        k = resolve_field(args.field)
        drafts = {k: drafts.get(k, [])}
    if args.hints:
        drafts = {"_hints": rec.get("hints", {}), **drafts}

    def human(d):
        for k, lst in d.items():
            if k == "_hints":
                print("【样本摘要提示（非原文，仅供对照）】")
                for hk, hv in lst.items():
                    print(f"   {hk}: {hv}")
                continue
            print(f"【{k}】")
            for c in lst:
                print(f"   - {c.get('value') or '(候选句)'} ⟵ {c['doc']} 第{c['page']}页「{c['quote'][:120]}」")
    _out(args, drafts, human)


def cmd_text(args):
    import re as _re
    from medins_lib.common import read_text
    from medins_lib.fetch import load_manifest
    from medins_lib.textextract import text_to_pages
    ws = _ws(args)
    pid = ws.get(args.pid)["pid"]
    docs = [d for d in load_manifest(ws, pid)["documents"] if d.get("text_file")]
    if args.doc:
        from medins_lib.records import find_doc
        docs = [find_doc(ws, pid, args.doc)]
    if not args.grep and not args.page:
        for d in docs:
            print(f"{d['file']}  ({d['doc_type_name']}, {d.get('pages') or 1}页) → {ws.root / d['text_file']}")
        return
    pat = _re.compile(args.grep) if args.grep else None
    hits = 0
    for d in docs:
        pages = text_to_pages(read_text(ws.root / d["text_file"]))
        for pno, page in enumerate(pages, 1):
            if args.page and pno != args.page:
                continue
            if pat is None:
                print(f"===== {d['file']} 第{pno}页 =====\n{page}")
                continue
            flat = _re.sub(r"\s+", "", page)
            last_end = -1
            for m in pat.finditer(flat):
                if m.start() < last_end:  # 与上一条摘录重叠，跳过
                    continue
                hits += 1
                a, b = max(0, m.start() - args.context), m.end() + args.context
                last_end = b
                print(f"[{d['file']} 第{pno}页] …{flat[a:b]}…")
                if hits >= args.limit:
                    print(f"（已达上限 {args.limit} 条）")
                    return


def cmd_record(args):
    from medins_lib.records import apply_patch, load_patch, load_record, summary
    ws = _ws(args)
    pid = ws.get(args.pid)["pid"]
    if args.action == "show":
        rec = load_record(ws, pid)
        if args.json:
            print(json.dumps(rec if not args.missing else summary(rec), ensure_ascii=False, indent=2))
            return
        s = summary(rec)
        print(f"{s['product']}：{s['status_counts']}；责任明细 {s['benefit_rows']} 行；费率表 {s['rate_tables']} 张")
        if s["missing_required"]:
            print("必填未处理：" + "、".join(s["missing_required"]))
        if not args.missing:
            from medins_lib.records import field_map
            fm = field_map()
            for k, f in rec["fields"].items():
                if f["status"] != "未填":
                    ev = "；".join(f"{e.get('doc')} p{e.get('page')}:{e.get('match')}" for e in f.get("evidence", []))
                    print(f"  [{f['status']}] {fm[k]['label']}：{f.get('value') or f.get('note')}  ⟵ {ev}")
        return
    if args.action == "set":
        item = {"field": args.field, "value": args.value or "", "status": args.status or "", "note": args.note or "",
                "evidence": [{"doc": args.doc, "quote": args.quote, "page": args.page}] if args.quote else []}
        items = [item]
    else:
        items = load_patch(Path(args.file))
    res = apply_patch(ws, pid, items, allow_unverified=args.allow_unverified)

    def human(d):
        for r in d:
            print(f"{'✔' if r['applied'] else '✘'} {r['field']} {r.get('status', '')}")
            for p in r["problems"]:
                print(f"     ✘ {p}")
            for w in r["warnings"]:
                print(f"     ⚠ {w}")
        print(f"写入 {sum(1 for r in d if r['applied'])}/{len(d)}")
    _out(args, res, human)
    return 0 if all(r["applied"] for r in res) else 1


def cmd_rates(args):
    from medins_lib.rates import block_to_table, parse_rate_file
    from medins_lib.records import apply_patch, find_doc
    from medins_lib.fetch import load_manifest
    ws = _ws(args)
    pid = ws.get(args.pid)["pid"]
    docs = [find_doc(ws, pid, args.doc)] if args.doc else [d for d in load_manifest(ws, pid)["documents"] if d["doc_type"] == "02" and d.get("text_file")]
    if not docs:
        raise MedinsError("没有可解析的费率表（02 类）文件")
    if args.action == "list":
        out = []
        for d in docs:
            for b in parse_rate_file(ws.root / d["text_file"]):
                out.append({"doc": d["file"], **{k: b[k] for k in ("block", "page", "context", "n_cols", "uniform", "columns_guess")},
                            "rows": len(b["rows"]), "first": b["rows"][0]["line"], "last": b["rows"][-1]["line"]})

        def human(dd):
            for b in dd:
                print(f"[{b['doc']}] 块{b['block']} 第{b['page']}页 {b['rows']}行×{b['n_cols']}列{'' if b['uniform'] else '(列数不齐)'}")
                print(f"     上下文：{' / '.join(b['context'][-4:])}")
                print(f"     首行：{b['first']}   末行：{b['last']}")
        _out(args, out, human)
        return
    d = docs[0]
    blocks = {b["block"]: b for b in parse_rate_file(ws.root / d["text_file"])}
    if args.block not in blocks:
        raise MedinsError(f"块号无效：{args.block}（先 rates list）")
    cols = [c.strip() for c in args.columns.split(",") if c.strip()]
    idx = [int(x) for x in args.col_index.split(",")] if args.col_index else None
    table = block_to_table(blocks[args.block], cols, idx, args.basis, d["file"], unit=args.unit)
    res = apply_patch(ws, pid, [{"field": "rate_table", "table": table}])
    _out(args, res, lambda dd: [print(f"{'✔' if r['applied'] else '✘'} 费率表 {r.get('status', '')} {'；'.join(r['problems'])}") for r in dd])


def cmd_fields(args):
    from medins_lib.records import schema_fields
    fs = [f for f in schema_fields() if not args.section or f["section"] == args.section]
    _out(args, fs, lambda d: [print(f"{f['key']:<24}{'*' if f.get('required') else ' '} {f['section']}｜{f['label']}：{f['desc']}") for f in d])


def cmd_validate(args):
    from medins_lib.validate import validate_all
    ws = _ws(args)
    rep = validate_all(ws, _pids(ws, args))

    def human(d):
        for p in d["products"]:
            print(f"■ {p['company']}·{p['product']}：error {len(p['errors'])}，warning {len(p['warnings'])}")
            for e in p["errors"][: args.limit]:
                print(f"   ✘ {e}")
            for w in p["warnings"][: args.limit]:
                print(f"   ⚠ {w}")
        print(f"合计 error {d['totals']['errors']}，warning {d['totals']['warnings']}；明细：outputs/校验报告.json")
    _out(args, rep, human)
    return 1 if (args.strict and rep["totals"]["errors"]) else 0


def cmd_compare(args):
    from medins_lib.compare import run_compare
    ws = _ws(args)
    pairs = [tuple(x.split(":", 1)) for x in (args.pair or [])]
    res = run_compare(ws, args.pids or None, pairs or None)
    _out(args, {"versions": [{"old": v["old"]["product_name"], "new": v["new"]["product_name"], "changes": len(v["changes"])} for v in res["versions"]],
                "cross_rows_differ": sum(1 for r in res["cross"]["rows"] if r["differs"])},
         lambda d: print(f"横向对比：{d['cross_rows_differ']} 个维度存在差异；版本对比 {len(d['versions'])} 组："
                         + "；".join(f"{v['old']}→{v['new']}({v['changes']}项变化)" for v in d["versions"]) + "\n结果：outputs/对比结果.json"))


def cmd_innovate(args):
    from medins_lib.innovation import needs_judgment, run_innovation
    ws = _ws(args)
    res = run_innovation(ws, args.pids or None)

    def human(d):
        b = d["baseline"]
        print(f"对照样本：市场样本库 {b['builtin_samples']} 款 + 本次研究 {b['workspace_products']} 款 + 同业对照 {b['peer_products']} 款"
              f"（某特征的对照产品少于 {b['min_peers']} 款时不判断首见/少见）")
        for pid, info in d["products"].items():
            top = [c for c in info["candidates"] if needs_judgment(c)]
            print(f"■ {info['company']}·{info['product']}：候选 {len(top)} 项")
            for c in top:
                print(f"   - {c['label']}：{c['verdict']}{'，本系列新增' if c.get('new_in_series') else ''}（{c['peers_with']}/{c['peers_total']}）{'；'.join(c['flags'])}")
        print("结果：outputs/创新点候选.json；请在 analysis.json 写研究者结论")
    _out(args, res, human)


def cmd_excel(args):
    from medins_lib.excel import build_workbook
    ws = _ws(args)
    p = build_workbook(ws, args.pids or None, Path(args.out) if args.out else None, args.title or "", getattr(args, "mode", "auto") or "auto")
    _out(args, {"excel": str(p)}, lambda d: print(f"Excel：{d['excel']}"))


def cmd_report(args):
    from medins_lib.report import build_report
    ws = _ws(args)
    r = build_report(ws, args.pids or None, title=args.title or "", docx=not args.no_docx)
    _out(args, r, lambda d: print(f"报告：{d['markdown']}" + (f"\nWord：{d['docx']}" if d.get("docx") else "") +
                                  "".join(f"\n  ⚠ {x}" for x in d["problems"][:20])))


def cmd_build(args):
    rc = cmd_validate(argparse.Namespace(**{**vars(args), "strict": False, "limit": 5}))
    cmd_compare(args)
    cmd_innovate(args)
    cmd_excel(args)
    if not args.json:
        print("对比表已完成。下一步：询问用户是否需要制作对应的分析报告（Word）和汇报 PPT，"
              "再用 deliver --report yes/no --ppt yes/no 登记；需要时运行 report / ppt。")
    return rc


def cmd_deliver(args):
    ws = _ws(args)
    cfg = ws.load()
    yes = {"yes": True, "y": True, "是": True, "需要": True, "no": False, "n": False, "否": False, "不需要": False}
    want = {k: yes[str(v).lower()] for k, v in (("report", args.report), ("ppt", args.ppt))}
    from medins_lib.common import now_iso
    cfg["deliverables"] = {**want, "asked_at": now_iso()}
    ws.save(cfg)
    _out(args, cfg["deliverables"], lambda d: print(f"已登记：分析报告 {'需要' if d['report'] else '不需要'}；汇报 PPT {'需要' if d['ppt'] else '不需要'}。"
                                                    "继续运行 next。"))


def cmd_ppt(args):
    from medins_lib.ppt import build_ppt
    ws = _ws(args)
    r = build_ppt(ws, args.pids or None, Path(args.out) if args.out else None, args.title or "",
                  "" if args.no_template else args.template, args.org or "")
    _out(args, r, lambda d: print(f"PPT：{d['pptx']}（{d['slides']} 页{'，模板 ' + d['template'] if d['template'] else ''}）" +
                                  "".join(f"\n  ⚠ {x}" for x in d["warnings"])))


def cmd_peers(args):
    from medins_lib.common import now_iso
    from medins_lib.innovation import MIN_PEERS, insufficient_features, peer_fields, suggest_peers
    ws = _ws(args)
    cfg = ws.load()
    if args.skip:
        if not args.reason:
            raise MedinsError("跳过补充同业对照需要 --reason 写明原因（会写入结论边界提示）")
        cfg["peer_skip"] = {"reason": args.reason, "at": now_iso()}
        ws.save(cfg)
        _out(args, cfg["peer_skip"], lambda d: print(f"已记录跳过原因：{d['reason']}。对比判断中“样本不足”的特征必须在 boundary 写明样本范围。继续运行 next。"))
        return
    lack = insufficient_features(ws)
    res = {"min_peers": MIN_PEERS, "features": [f["label"] for f in lack], "peer_fields": peer_fields(ws),
           "current_peers": [ws.get(p)["product_name"] for p in ws.peer_pids()], "suggestions": suggest_peers(ws, args.limit)}

    def human(d):
        print(f"需要补对照的特征：{'、'.join(d['features']) or '无（对照样本已足够）'}")
        from medins_lib.records import field_map
        fm = field_map()
        print(f"同业对照产品只需整理这些字段：{'、'.join(fm[k]['label'] if k in fm else k for k in d['peer_fields'])}")
        if d["current_peers"]:
            print(f"已登记的同业对照：{'、'.join(d['current_peers'])}")
        print("推荐的同业对照产品（同类型、不同系列；优先有官网链接）：")
        for s_ in d["suggestions"]:
            print(f"  {s_['id']}  {s_['company']}·{s_['product_name']}  {s_['product_type'] or ''}  {(s_['source_urls'] or [''])[0]}")
        print(f"登记：python <SKILL_DIR>/scripts/medins.py register -w \"{ws.root}\" --from-catalog <id> --role peer")
        print(f"跳过：python <SKILL_DIR>/scripts/medins.py peers -w \"{ws.root}\" --skip --reason \"<原因>\"")
    _out(args, res, human)


def cmd_market(args):
    from medins_lib.innovation import load_features
    from medins_lib.market import MIN_QUERIES, add_hit, load_scan, mark_none, pending_features, queries_for
    ws = _ws(args)
    if args.action == "add":
        h = add_hit(ws, args.feature, args.product or "", args.url or "", args.quote or "", args.launch or "", args.file,
                    args.note or "", args.allow_unverified)
        _out(args, h, lambda d: print(f"已记录 {args.feature}：{d['product']}（引文核验：{d.get('match')}，证据 {d.get('evidence_level')}）"
                                      f"{chr(10) + '  ⚠ ' + d['error'] if d.get('error') else ''}"))
        return
    if args.action == "none":
        f = mark_none(ws, args.feature, args.query or [], args.note or "")
        _out(args, f, lambda d: print(f"已记录 {args.feature}：全网检索未见同类（检索词 {len(d['queries'])} 组）"))
        return
    fm = {f["key"]: f for f in load_features()}
    scan = load_scan(ws).get("features", {})
    pend = pending_features(ws)
    res = {"pending": [{"feature": f["key"], "label": f["label"], "queries": queries_for(f)} for f in pend],
           "recorded": {k: {"label": v.get("label"), "status": v.get("status"), "hits": [h["product"] for h in v.get("hits", [])],
                            "queries": v.get("queries", [])} for k, v in scan.items()}}

    def human(d):
        if d["pending"]:
            print(f"待全网核验的特征（每个特征：找到同类就 market add；换 ≥{MIN_QUERIES} 组关键词仍未找到就 market none）：")
            for x in d["pending"]:
                print(f"  {x['feature']}  {x['label']}")
                print(f"    建议检索词：{'；'.join(x['queries'])}")
        else:
            print("没有待核验的特征")
        for k, v in d["recorded"].items():
            st = f"命中 {len(v['hits'])} 款：{'、'.join(v['hits'])}" if v["hits"] else f"检索未见（{'；'.join(v['queries'])}）"
            print(f"  [已记录] {k} {fm.get(k, {}).get('label', '')}：{st}")
        print(f"记录命中：python <SKILL_DIR>/scripts/medins.py market add -w \"{ws.root}\" --feature <key> --product \"<公司·产品>\" "
              "--url <链接> --quote \"<页面原文>\" --launch <YYYY-MM>")
        print(f"记录未见：python <SKILL_DIR>/scripts/medins.py market none -w \"{ws.root}\" --feature <key> --query \"<关键词1>\" "
              "--query \"<关键词2>\" --note \"<查过的来源>\"")
    _out(args, res, human)


def cmd_next(args):
    from medins_lib.guide import next_steps
    ws = _ws(args)
    r = next_steps(ws)

    def human(d):
        for p in d.get("progress", []):
            print(f"  · {p['product']}（{p['pid']}）：资料 {p['documents']} 份；必填待处理 {p['required_pending']}；全部待处理 {p['all_pending']}")
        print(f"当前阶段：{d['stage']}{'（最后一步）' if d['done'] else ''}")
        for i, s in enumerate(d["steps"], 1):
            print(f"{i}. {s['do']}")
            if s.get("cmd"):
                print(f"   {s['cmd']}")
        print("完成后再次运行 next，直到提示“完成”。")
    _out(args, r, human)


def cmd_task(args):
    from medins_lib.common import write_json as _wj
    from medins_lib.guide import task_card
    ws = _ws(args)
    c = task_card(ws, args.pid, args.field)
    if c.get("done"):
        _out(args, c, lambda d: print(d["message"]))
        return
    patch = ws.root / "patches" / f"patch_{c['field']}.json"
    _wj(patch, c["patch_template"])
    c["patch_file"] = str(patch)
    c["apply_cmd"] = c["apply_cmd"].replace(f"patch_{c['field']}.json", f'"{patch}"')

    def human(d):
        print(f"【任务】{d['product']} · 字段“{d['label']}”（{d['field']}{'，必填' if d['required'] else ''}）"
              f"  剩余必填 {d['remaining']['required']} / 全部 {d['remaining']['all']}")
        print(f"口径：{d['definition']}")
        print(f"示例（详细程度参考）：{d['example']}")
        print(f"去哪找：{d['where']}")
        if d["search_cmd"]:
            print(f"① 检索原文：{d['search_cmd']}")
        if d["candidates"]:
            print("   候选原文（可直接用作引文，务必读上下文确认含义）：")
            for x in d["candidates"]:
                print(f"   - {x['doc']} 第{x['page']}页「{x['quote'][:150]}」" + (f" → 候选值：{x['value']}" if x.get("value") else ""))
        if d.get("current"):
            print(f"   当前已有：[{d['current']['status']}] {d['current'].get('value')}")
        print(f"② 编辑补丁文件（已生成模板）：{d['patch_file']}")
        print("   找不到原文时改为：" + json.dumps(d["if_not_found"], ensure_ascii=False))
        print(f"③ 写入并核验：{d['apply_cmd']}")
        print("规则：" + "；".join(d["rules"]))
    _out(args, c, human)


def cmd_template(args):
    from medins_lib.guide import write_analysis_template, write_template
    ws = _ws(args)
    if args.analysis:
        p = write_analysis_template(ws)
        _out(args, {"file": str(p)}, lambda d: print(f"analysis 骨架：{d['file']}（按 _说明 填写；填完可删除以 _ 开头的键）"))
        return
    p = write_template(ws, args.pid)
    _out(args, {"file": str(p)}, lambda d: print(f"补丁骨架：{d['file']}\n逐项填写 value 与 evidence（候选引文已预填，不对就删改），"
                                                f"不能确认的改成 status=待核 并写 note；未填写的项 apply 时自动跳过。\n"
                                                f"写完后：python <SKILL_DIR>/scripts/medins.py record apply -w \"{ws.root}\" --pid {args.pid} --file \"{d['file']}\""))


def cmd_catalog_add(args):
    from medins_lib.catalog import add_to_catalog, company_info
    ws = _ws(args) if args.workspace else None
    c = company_info(args.company)
    e = add_to_catalog({"company": c["short"] if c else args.company, "insurer_full": c["full"] if c else "",
                        "product_name": args.product, "series": args.series or "", "aliases": args.alias or [],
                        "clause_names": args.clause_name or [], "access_channel": args.access or "", "body_type": args.body_type or "",
                        "launch": args.launch or "", "product_type": args.product_type or "", "origin": "user"},
                       ws.root if ws else None, user_level=args.user_level or ws is None)
    _out(args, e, lambda d: print(f"已加入目录：{d['id']} {d['company']}·{d['product_name']}"))


# ---------------- 参数 ----------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="medins.py", description="医疗险产品研究：检索、归档、标准化、对比、创新点、Excel/报告")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-w", "--workspace", help="工作区目录（默认环境变量 MEDINS_WORKSPACE 或 ./medins_workspace）")
    common.add_argument("--json", action="store_true", help="输出 JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        p = sub.add_parser(name, parents=[common], help=help_, description=help_)
        p.set_defaults(fn=fn)
        return p

    add("doctor", cmd_doctor, "检查运行环境与依赖")
    add("next", cmd_next, "引导模式：检查进度，给出当前唯一应做的下一步与命令")
    p = add("task", cmd_task, "字段任务卡：口径、示例、去哪找、检索命令、候选原文，并生成补丁模板")
    p.add_argument("--pid", required=True)
    p.add_argument("--field", help="指定字段（默认取下一个待填的必填字段）")
    p = add("template", cmd_template, "生成全部待填字段的补丁骨架，或 --analysis 生成研究判断骨架")
    p.add_argument("--pid")
    p.add_argument("--analysis", action="store_true")
    p = add("init", cmd_init, "初始化研究工作区")
    p.add_argument("--title", help="研究标题（用于输出文件名）")
    p = add("resolve", cmd_resolve, "模糊检索产品（内置目录 + 工作区目录）")
    p.add_argument("query")
    p.add_argument("--top", type=int, default=8)
    p = add("queries", cmd_queries, "生成联网检索词与资料入口提示")
    p.add_argument("product")
    p.add_argument("--company")
    p.add_argument("--clause-name", action="append")
    p = add("search", cmd_search, "内置网页检索（兜底；优先使用 Agent 自带搜索）")
    p.add_argument("product")
    p.add_argument("--company")
    p.add_argument("--engines", default="bing,duckduckgo")
    p.add_argument("--max-queries", type=int, default=8)
    p = add("discover", cmd_discover, "从网页（URL 或保存的 HTML）中发现条款/费率表等附件链接")
    p.add_argument("source")
    p.add_argument("--product", help="产品名（用于相关度排序）")
    p.add_argument("--base-url", help="本地 HTML 的原始网址（解析相对链接）")
    p.add_argument("--save", help="把结果保存为 JSON，供 fetch --from-discover 使用")
    p.add_argument("--insecure", action="store_true")
    p = add("register", cmd_register, "在工作区登记产品（可从目录导入；--pid 更新已登记产品）")
    p.add_argument("--pid", help="更新已登记产品（如补登备案条款名）")
    p.add_argument("--from-catalog", help="目录 id（resolve 输出）")
    p.add_argument("--company")
    p.add_argument("--product")
    p.add_argument("--insurer-full")
    p.add_argument("--series")
    p.add_argument("--version")
    p.add_argument("--access", help="健告类型/准入通道")
    p.add_argument("--body-type", help="次标体/健康体/专项带病体/普惠")
    p.add_argument("--launch", help="上市时间 YYYY-MM")
    p.add_argument("--clause-name", action="append", help="备案条款名（可多次）")
    p.add_argument("--add-to-catalog", action="store_true", help="同时写入工作区目录，便于后续模糊检索")
    p.add_argument("--role", choices=["research", "peer"], help="peer=同业对照产品（只作创新点识别的对照样本，不进对比表/报告）")
    add("list", cmd_list, "列出工作区产品")
    p = add("fetch", cmd_fetch, "下载并归档资料（自动分类、哈希、版本核对、证据等级）")
    p.add_argument("--pid", required=True)
    p.add_argument("urls", nargs="*")
    p.add_argument("--type", help="指定资料类型编号 01–09")
    p.add_argument("--title", help="指定文件标题")
    p.add_argument("--referer")
    p.add_argument("--evidence-level", choices=list("ABCDabcd"))
    p.add_argument("--from-discover", help="discover --save 生成的 JSON")
    p.add_argument("--types", help="与 --from-discover 合用，只下载这些类型，如 01,02,03,04,05")
    p.add_argument("--insecure", action="store_true", help="跳过 SSL 证书校验（仅限确认的官方站点）")
    p.add_argument("--note")
    p = add("import", cmd_import, "导入本地文件/目录（原件不动，复制归档；自动恢复来源索引中的链接）")
    p.add_argument("--pid", required=True)
    p.add_argument("paths", nargs="+")
    p.add_argument("--type")
    p.add_argument("--title")
    p.add_argument("--source-url")
    p.add_argument("--evidence-level", choices=list("ABCDabcd"))
    p = add("gap", cmd_gap, "登记资料缺口或“非缺文件”说明")
    p.add_argument("--pid", required=True)
    p.add_argument("--type", required=True, help="资料类型编号，如 03")
    p.add_argument("--status", required=True, help="无健康告知（非缺文件）/动态投保页核验说明（非逐字原件）/未找到可确认文件/待取得原件/不适用")
    p.add_argument("--note")
    p.add_argument("--evidence")
    p = add("verify-docs", cmd_verify_docs, "登记备案条款名后重新核对文件版本")
    p.add_argument("--pid", nargs="*")
    p = add("extract", cmd_extract, "抽取文本并生成字段草稿（--apply-safe 写入高精度字段）")
    p.add_argument("--pid", nargs="*")
    p.add_argument("--apply-safe", action="store_true")
    p = add("drafts", cmd_drafts, "查看字段草稿（候选原文句）")
    p.add_argument("--pid", required=True)
    p.add_argument("--field")
    p.add_argument("--hints", action="store_true", help="同时显示样本摘要目录中的摘要（非原文）")
    p = add("text", cmd_text, "列出/检索归档原文（带页码），供逐字摘引")
    p.add_argument("--pid", required=True)
    p.add_argument("--doc")
    p.add_argument("--grep", help="正则；在去空白后的文本中检索")
    p.add_argument("--page", type=int)
    p.add_argument("--context", type=int, default=80)
    p.add_argument("--limit", type=int, default=40)
    p = add("record", cmd_record, "查看/写入标准化字段（写入时核验引文）")
    p.add_argument("action", choices=["show", "set", "apply"])
    p.add_argument("--pid", required=True)
    p.add_argument("--missing", action="store_true")
    p.add_argument("--field")
    p.add_argument("--value")
    p.add_argument("--status", choices=["原文核验", "规则抽取", "推定", "待核", "不适用"])
    p.add_argument("--note")
    p.add_argument("--doc")
    p.add_argument("--quote")
    p.add_argument("--page", type=int)
    p.add_argument("--file", help="apply 用：JSON 补丁文件")
    p.add_argument("--allow-unverified", action="store_true", help="引文找不到时以“待复核”写入（不推荐）")
    p = add("rates", cmd_rates, "解析费率表（list 列出分块；import 按列写入记录）")
    p.add_argument("action", choices=["list", "import"])
    p.add_argument("--pid", required=True)
    p.add_argument("--doc")
    p.add_argument("--block", type=int)
    p.add_argument("--columns", default="", help="列名，逗号分隔，如 计划一,计划二")
    p.add_argument("--col-index", help="取原表第几列（0起），逗号分隔，如 0,1")
    p.add_argument("--basis", default="", help="口径，如 有社保·年交·含等待期·首次投保")
    p.add_argument("--unit", default="元")
    p = add("fields", cmd_fields, "查看标准化字段口径")
    p.add_argument("--section")
    for name, fn, h in (("validate", cmd_validate, "校验资料、引文与口径"), ("compare", cmd_compare, "横向对比与新旧版本对比"),
                        ("innovate", cmd_innovate, "创新点候选识别"), ("excel", cmd_excel, "生成 Excel 对比表"),
                        ("report", cmd_report, "生成分析报告（Markdown/Word）"), ("build", cmd_build, "一键：校验→对比→创新点→Excel 对比表"),
                        ("ppt", cmd_ppt, "生成汇报 PPT（16:9；可 --template 套用自有模板）")):
        p = add(name, fn, h)
        p.add_argument("--pids", nargs="*")
        if name in ("validate", "build"):
            p.add_argument("--strict", action="store_true", help="有 error 时返回非 0")
            p.add_argument("--limit", type=int, default=30)
        if name in ("compare", "build"):
            p.add_argument("--pair", action="append", help="指定版本对：旧pid:新pid（可多次）")
        if name in ("excel", "build", "ppt"):
            p.add_argument("--out")
        if name in ("excel", "build"):
            p.add_argument("--mode", choices=["auto", "series", "market"], default="auto",
                           help="对比表版式：series 同系列版本对比 / market 多产品横比；默认按样本自动选择")
        if name in ("excel", "report", "build", "ppt"):
            p.add_argument("--title")
        if name == "report":
            p.add_argument("--no-docx", action="store_true")
        if name == "ppt":
            p.add_argument("--template", help="自有 .pptx 模板（默认读 ~/.medins-research/config.json 的 ppt_template）")
            p.add_argument("--no-template", action="store_true", help="忽略默认模板，使用内置版式")
            p.add_argument("--org", help="封面署名（部门/机构）")
    p = add("market", cmd_market, "全网核验创新点：list 查看待核验特征与检索词；add 记录同类产品（自动核验页面原文）；none 记录检索未见")
    p.add_argument("action", nargs="?", default="list", choices=["list", "add", "none"])
    p.add_argument("--feature", help="特征 key，如 general_pre_payable（market list 可查看）")
    p.add_argument("--product", help="同类产品：公司·产品名")
    p.add_argument("--url", help="页面或条款链接（脚本会下载核验）")
    p.add_argument("--file", help="动态页面无法下载时：浏览器另存的 .html/.pdf 文件")
    p.add_argument("--quote", help="页面中证明具备该特征的原文（逐字）")
    p.add_argument("--launch", help="该产品上市时间 YYYY-MM（用于判断谁更早）")
    p.add_argument("--query", action="append", help="none 用：检索词（至少 2 组，可多次）")
    p.add_argument("--note", help="none 用：查过哪些来源；add 用：补充说明")
    p.add_argument("--allow-unverified", action="store_true", help="页面无法抓取时仍记录命中（标“未核验”）")
    p = add("peers", cmd_peers, "（可选）同业对照：推荐可整理的同类产品；--skip --reason 说明原因后跳过")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--skip", action="store_true")
    p.add_argument("--reason")
    p = add("deliver", cmd_deliver, "登记用户是否需要分析报告与汇报 PPT（对比表完成后询问用户）")
    p.add_argument("--report", required=True, choices=["yes", "no"])
    p.add_argument("--ppt", required=True, choices=["yes", "no"])
    p = add("catalog-add", cmd_catalog_add, "把产品加入目录（工作区或用户级），供后续模糊检索")
    p.add_argument("--company", required=True)
    p.add_argument("--product", required=True)
    p.add_argument("--series")
    p.add_argument("--alias", action="append")
    p.add_argument("--clause-name", action="append")
    p.add_argument("--access")
    p.add_argument("--body-type")
    p.add_argument("--launch")
    p.add_argument("--product-type")
    p.add_argument("--user-level", action="store_true", help="写入 ~/.medins-research/catalog.json（所有项目可用）")
    return ap


def main(argv=None) -> int:
    setup_utf8()
    args = build_parser().parse_args(argv)
    try:
        rc = args.fn(args)
        return int(rc or 0)
    except MedinsError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
