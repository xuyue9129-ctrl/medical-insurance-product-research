"""引导模式：让任何 Agent 按“下一步做什么”逐步完成研究，不依赖模型自己规划。

- next_steps(ws)：检查工作区状态，给出当前唯一应做的下一步与可直接执行的命令
- task_card(ws, pid, field)：为下一个待填字段生成任务卡（口径、示例、去哪找、检索命令、候选原文、补丁模板）
- write_template(ws, pid)：为所有待填字段生成补丁骨架（含候选引文），Agent 只需改值、删多余候选
- lint_field(key, value)：细节检查（缺金额、缺比例、过短等），validate 会调用
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .common import read_json, write_json
from .doctypes import CORE_TYPES, DOC_TYPES
from .records import field_map, load_record, schema_fields
from .workspace import Workspace

DONE = {"原文核验", "推定", "待核", "不适用"}


def lint_field(key: str, value: str) -> list[str]:
    f = field_map().get(key, {})
    rule = f.get("lint") or {}
    out = []
    v = value or ""
    if rule.get("need") and v and not re.search(rule["need"], v):
        out.append(f"{f.get('label', key)}：取值缺少必要要素（应包含：{rule['need'].replace('|', ' / ')}），请按口径补全")
    if rule.get("min_len") and v and len(v) < rule["min_len"]:
        out.append(f"{f.get('label', key)}：取值过于简略（{len(v)}字），请写到可直接对比的程度")
    return out


def _cmd(ws: Workspace, sub: str) -> str:
    return f'python <SKILL_DIR>/scripts/medins.py {sub} -w "{ws.root}"'


def _pending_fields(rec: dict, required_only: bool = False, only: list[str] | None = None) -> list[dict]:
    """待处理字段；only 给定时（同业对照产品）只看这些字段，且都视为必填。"""
    out = []
    for f in schema_fields():
        st = rec["fields"].get(f["key"], {}).get("status", "未填")
        if st in DONE:
            continue
        if only is not None:
            if f["key"] in only:
                out.append(f)
            continue
        if required_only and not f.get("required"):
            continue
        out.append(f)
    return out


def _scope(ws: Workspace, p: dict) -> list[str] | None:
    if p.get("role") != "peer":
        return None
    from .innovation import peer_fields
    return peer_fields(ws)


def next_steps(ws: Workspace) -> dict:
    """返回 {stage, done, steps:[{do, cmd}], progress}。按顺序检查，只给出当前最该做的一步。"""
    from .fetch import completeness, load_manifest
    cfg = ws.load(required=False)
    if not cfg:
        return {"stage": "初始化", "done": False, "steps": [{"do": "初始化研究工作区", "cmd": _cmd(ws, "init") + ' --title "<研究标题>"'}]}
    prods = cfg.get("products", {})
    if not prods:
        return {"stage": "登记产品", "done": False, "steps": [
            {"do": "模糊检索产品，向用户确认是哪一款/哪些版本", "cmd": 'python <SKILL_DIR>/scripts/medins.py resolve "<产品名>"'},
            {"do": "登记确认的产品（目录内用 id；目录外用公司+产品名）", "cmd": _cmd(ws, "register") + " --from-catalog <id>"}]}
    progress = []
    for pid, p in prods.items():
        m = load_manifest(ws, pid)
        docs = m["documents"]
        rec = load_record(ws, pid)
        scope = _scope(ws, p)
        pend_req = _pending_fields(rec, True, scope)
        pend_all = _pending_fields(rec, False, scope)
        progress.append({"pid": pid, "product": p["product_name"] + ("（同业对照）" if scope is not None else ""), "documents": len(docs),
                         "required_pending": len(pend_req), "all_pending": len(pend_all)})
        name = p["product_name"] + ("（同业对照，只需条款与对照字段）" if scope is not None else "")
        if not docs:
            return {"stage": "收集资料", "done": False, "progress": progress, "steps": [
                {"do": f"【{name}】有本地资料就导入（推荐，最快）", "cmd": _cmd(ws, "import") + f' --pid {pid} "<本地资料目录>"'},
                {"do": f"【{name}】否则生成检索词，用你的联网搜索找官方条款/费率表/投保须知/严重既往症清单的 PDF 链接",
                 "cmd": f'python <SKILL_DIR>/scripts/medins.py queries "{name}" --company {p["company"]}'},
                {"do": "拿到 PDF 链接后下载归档（可一次给多个链接）", "cmd": _cmd(ws, "fetch") + f" --pid {pid} <PDF链接1> <PDF链接2>"},
                {"do": "只有产品页链接时，先列出页面附件再下载", "cmd": f'python <SKILL_DIR>/scripts/medins.py discover <产品页URL> --product "{name}" --save links.json'}]}
        if not p.get("clause_names"):
            detected = sorted({c for d in docs for c in d.get("detected_clause_names") or []})
            args = " ".join(f'--clause-name "{c}"' for c in detected) or '--clause-name "<条款首页的备案条款名>"'
            return {"stage": "核对版本", "done": False, "progress": progress, "steps": [
                {"do": f"【{name}】确认条款属于本产品本版本后，登记备案条款名（下面已填入条款首页识别结果，不对就改）",
                 "cmd": _cmd(ws, "register") + f" --pid {pid} {args}"},
                {"do": "重新核对全部文件版本", "cmd": _cmd(ws, "verify-docs") + f" --pid {pid}"}]}
        comp = completeness(m)
        missing = [c for c, s in comp.items() if s.startswith("缺失") and (scope is None or c == "01")]
        if missing:
            c = missing[0]
            return {"stage": "补齐资料", "done": False, "progress": progress, "steps": [
                {"do": f"【{name}】缺少 {c} {DOC_TYPES[c]}：先按检索词再找一次；确实找不到就登记原因（免健告产品的健康告知用“无健康告知（非缺文件）”）",
                 "cmd": _cmd(ws, "gap") + f' --pid {pid} --type {c} --status "未找到可确认文件" --note "<已查过哪些来源>"'}]}
        if not rec.get("drafts"):
            return {"stage": "抽取", "done": False, "progress": progress, "steps": [
                {"do": "抽取原文并自动写入高精度字段", "cmd": _cmd(ws, "extract") + " --apply-safe"}]}
        if pend_req or pend_all:
            f = (pend_req or pend_all)[0]
            return {"stage": "逐字段整理", "done": False, "progress": progress, "steps": [
                {"do": f"【{name}】按任务卡完成字段“{f['label']}”（读原文→写补丁→apply），完成后再运行 next",
                 "cmd": _cmd(ws, "task") + f" --pid {pid}"},
                {"do": "（可选）一次生成全部待填字段的补丁骨架，逐项改值后一起 apply",
                 "cmd": _cmd(ws, "template") + f" --pid {pid}"}]}
    rep = read_json(ws.outputs_dir / "校验报告.json")
    if rep is None or rep.get("totals", {}).get("errors", 1) or {r["pid"] for r in rep.get("products", [])} != set(prods):
        return {"stage": "校验", "done": False, "progress": progress, "steps": [
            {"do": "运行校验；有 error 就按提示逐条修正（重新 record apply 对应字段），直到 error=0", "cmd": _cmd(ws, "validate")}]}
    inn = read_json(ws.outputs_dir / "创新点候选.json")
    if inn is None:
        return {"stage": "对比与创新点", "done": False, "progress": progress, "steps": [
            {"do": "生成横向/版本对比与创新点候选", "cmd": _cmd(ws, "compare") + " && " + _cmd(ws, "innovate")}]}
    from .market import MIN_QUERIES, pending_features, signature
    base = inn.get("baseline") or {}
    if {x["pid"] for x in base.get("peers", [])} != set(ws.peer_pids()) or base.get("market_signature", "") != signature(ws):
        return {"stage": "重新识别创新点", "done": False, "progress": progress, "steps": [
            {"do": "全网核验记录或同业对照有更新，重新运行创新点识别（命中的同类产品会计入对照）", "cmd": _cmd(ws, "innovate")}]}
    pend = pending_features(ws)
    if pend:
        return {"stage": "全网核验创新点", "done": False, "progress": progress, "steps": [
            {"do": f"判断创新要对照整个市场，不能只看内置样本。以下候选特征需要联网核验：{'、'.join(f['label'] + '（' + f['key'] + '）' for f in pend)}。"
                   "先查看每个特征的建议检索词", "cmd": _cmd(ws, "market")},
            {"do": "逐个特征用你的联网搜索检索全市场（保险公司官网信息披露、行业协会产品库、持牌中介平台的条款与产品页）。"
                   "找到具备该特征的其他产品就记录，脚本会下载页面并核验原文；同一特征找到 3 款即可停止",
             "cmd": _cmd(ws, "market add") + ' --feature <特征key> --product "<公司·产品>" --url <链接> --quote "<页面原文逐字>" --launch <YYYY-MM>'},
            {"do": f"换用至少 {MIN_QUERIES} 组关键词仍未找到，记录“检索未见”及查过的来源",
             "cmd": _cmd(ws, "market none") + ' --feature <特征key> --query "<关键词1>" --query "<关键词2>" --note "<查过的来源>"'}]}
    if not comparison_judged(ws):
        steps = [{"do": "生成 analysis.json 骨架（已填好版本对与候选特征）", "cmd": _cmd(ws, "template") + " --analysis"}]             if not ws.analysis_path.exists() else []
        steps.append({"do": f"打开 {ws.analysis_path}，先完成对比判断：version_notes（每对新旧版本的主要变化）与 innovations"
                            "（每个候选特征的 verdict/summary/boundary）；多产品横比可再写 findings。含数字的句子都要带 [[pid:字段]] 引用。"
                            "参考：outputs/对比结果.json、outputs/创新点候选.json", "cmd": ""})
        return {"stage": "撰写对比判断", "done": False, "progress": progress, "steps": steps}
    outs = deliverables_built(ws)
    if not outs["excel"]:
        return {"stage": "出对比表", "done": False, "progress": progress, "steps": [
            {"do": "生成 Excel 对比表（同系列与多产品自动选择版式，可加 --mode series/market）", "cmd": _cmd(ws, "build")}]}
    want = (cfg.get("deliverables") or {})
    if "report" not in want or "ppt" not in want:
        return {"stage": "询问报告与PPT", "done": False, "progress": progress, "steps": [
            {"do": f"对比表已生成（{outs['excel']}）。现在必须先问用户：“是否需要制作对应的分析报告（Word）和汇报 PPT？”"
                   "得到回答后按用户选择登记（yes/no），不要替用户决定", "cmd": _cmd(ws, "deliver") + " --report yes --ppt yes"}]}
    if (want.get("report") or want.get("ppt")) and not narrative_filled(ws):
        steps = [{"do": "补充 analysis.json 骨架中的报告正文键", "cmd": _cmd(ws, "template") + " --analysis"}]             if not (read_json(ws.analysis_path, {}) or {}).get("sections") else []
        steps.append({"do": f"在 {ws.analysis_path} 写报告正文：sections.scope/underwriting/overview/renewal_pricing/benefits（各段研究判断）、"
                            "overall.summary（主要结论）、overall.trends 与 overall.risks（各 3–5 条，每条首句即观点）。"
                            "含数字的句子都要带 [[pid:字段]] 引用", "cmd": ""})
        return {"stage": "撰写报告正文", "done": False, "progress": progress, "steps": steps}
    if want.get("report") and not outs["report"]:
        return {"stage": "生成报告", "done": False, "progress": progress, "steps": [
            {"do": "生成分析报告（Markdown + Word）；输出的自检提示要回到 analysis.json 补引用后重跑", "cmd": _cmd(ws, "report")}]}
    if want.get("ppt") and not outs["ppt"]:
        return {"stage": "生成PPT", "done": False, "progress": progress, "steps": [
            {"do": "生成汇报 PPT（用户有自己的模板时加 --template <模板.pptx>）", "cmd": _cmd(ws, "ppt")}]}
    files = [x for x in (outs["excel"], outs["report"], outs["ppt"]) if x]
    return {"stage": "完成", "done": True, "progress": progress, "steps": [
        {"do": "全部产物已生成，向用户汇报以下文件、校验结果中的 warning 与待核事项；之后若改动字段或判断，重新运行 build / report / ppt",
         "cmd": "；".join(files)}]}


def deliverables_built(ws: Workspace) -> dict:
    """outputs 中最新的对比表 / 报告 / PPT（没有则为空串）。"""
    def latest(pattern):
        fs = sorted(ws.outputs_dir.glob(pattern), key=lambda f: f.stat().st_mtime) if ws.outputs_dir.exists() else []
        return str(fs[-1]) if fs else ""
    return {"excel": latest("*_对比表_*.xlsx"), "report": latest("*_分析报告_*.md"), "ppt": latest("*_汇报_*.pptx")}


def comparison_judged(ws: Workspace) -> bool:
    """有版本对或创新点候选时，要求至少写了一条版本解读或创新点结论。"""
    inn = read_json(ws.outputs_dir / "创新点候选.json", {}) or {}
    cmp = read_json(ws.outputs_dir / "对比结果.json", {}) or {}
    from .innovation import needs_judgment
    need = bool(cmp.get("versions")) or any(needs_judgment(c) for info in (inn.get("products") or {}).values() for c in info["candidates"])
    if not need:
        return True
    a = read_json(ws.analysis_path, {}) or {}
    notes = [v if isinstance(v, str) else (v or {}).get("summary", "") for v in (a.get("version_notes") or {}).values()]
    summ = [j.get("summary", "") for lst in (a.get("innovations") or {}).values() if isinstance(lst, list) for j in lst]
    return any(isinstance(t, str) and t.strip() for t in notes + summ)


def narrative_filled(ws: Workspace) -> bool:
    a = read_json(ws.analysis_path, {}) or {}
    texts = list((a.get("sections") or {}).values()) + [(a.get("overall") or {}).get("summary", "")]
    return any(isinstance(t, str) and t.strip() for t in texts)


def task_card(ws: Workspace, pid: str, field: str | None = None) -> dict:
    from .records import resolve_field
    p = ws.get(pid)
    rec = load_record(ws, p["pid"])
    fm = field_map()
    scope = _scope(ws, p)
    if field:
        f = fm[resolve_field(field)]
    else:
        pend = _pending_fields(rec, True, scope) or _pending_fields(rec, False, scope)
        if not pend:
            return {"pid": p["pid"], "done": True, "message": "全部字段已处理，运行 next 进入下一阶段"}
        f = pend[0]
    k = f["key"]
    drafts = (rec.get("drafts") or {}).get(k, [])[:4]
    cur = rec["fields"].get(k, {})
    grep = f.get("grep") or ""
    where = "、".join(f"{c} {DOC_TYPES[c]}" for c in f.get("where", [])) or "综合各资料归纳"
    tmpl = {"field": k, "value": "<按口径写：数值+条件+例外>",
            "evidence": [{"doc": (drafts[0]["doc"] if drafts else "<文件名或唯一片段>"),
                          "page": (drafts[0]["page"] if drafts else None),
                          "quote": (drafts[0]["quote"] if drafts else "<从 text 命令输出中逐字复制>")}]}
    remaining = len(_pending_fields(rec, True, scope)), len(_pending_fields(rec, False, scope))
    return {
        "pid": p["pid"], "product": p["product_name"] + ("（同业对照）" if scope is not None else ""), "field": k, "label": f["label"],
        "required": bool(f.get("required")) or scope is not None,
        "definition": f.get("desc", ""), "example": f.get("example", ""), "where": where,
        "search_cmd": (_cmd(ws, "text") + f' --pid {p["pid"]} --grep "{grep}"') if grep else "",
        "candidates": drafts, "current": cur if cur.get("status") != "未填" else None,
        "patch_template": [tmpl],
        "if_not_found": {"field": k, "status": "待核", "note": "<查过哪些文件、为何无法确认>"},
        "if_inference": {"field": k, "status": "推定", "value": "<归纳>", "note": "<依据>", "evidence": []},
        "apply_cmd": _cmd(ws, "record apply") + f' --pid {p["pid"]} --file patch_{k}.json',
        "remaining": {"required": remaining[0], "all": remaining[1]},
        "rules": ["quote 必须从 text 命令的输出中逐字复制，不要改写、不要拼接不相邻的句子（可用……连接同页两段）",
                  "value 中的每个数字都应能在引文里找到；多计划/多责任用“；”分开写全",
                  "找不到原文就用 if_not_found（待核+原因），不要凭经验填写"],
    }


def write_template(ws: Workspace, pid: str, out: Path | None = None) -> Path:
    p = ws.get(pid)
    rec = load_record(ws, p["pid"])
    items = []
    for f in _pending_fields(rec, False, _scope(ws, p)):
        drafts = (rec.get("drafts") or {}).get(f["key"], [])[:3]
        items.append({"field": f["key"], "_字段": f["label"], "_必填": bool(f.get("required")), "_口径": f.get("desc", ""),
                      "_示例": f.get("example", ""), "_去哪找": "、".join(DOC_TYPES[c] for c in f.get("where", [])),
                      "_检索词": f.get("grep", ""),
                      "value": "", "evidence": [{"doc": d["doc"], "page": d["page"], "quote": d["quote"]} for d in drafts],
                      "_候选值": [d["value"] for d in drafts if d.get("value")]})
    out = out or (ws.root / "patches" / f"{p['pid']}_待填.json")
    write_json(out, items)
    return out


def analysis_filled(ws: Workspace) -> bool:
    a = read_json(ws.analysis_path, {}) or {}
    texts = list((a.get("sections") or {}).values()) + [(a.get("overall") or {}).get("summary", "")]
    texts += [j.get("summary", "") for lst in (a.get("innovations") or {}).values() if isinstance(lst, list) for j in lst]
    return any(isinstance(t, str) and t.strip() for t in texts)


def write_analysis_template(ws: Workspace) -> Path:
    from .innovation import needs_judgment
    from .market import describe
    inn = read_json(ws.outputs_dir / "创新点候选.json", {}) or {}
    cmp = read_json(ws.outputs_dir / "对比结果.json", {}) or {}
    data = {
        "_说明": "每段判断里含数字的句子都要加 [[pid:字段]] 引用；verdict 只能填 真创新/差异化/跟进/营销口径；boundary 写样本范围与不能推出的结论。填完删除所有以 _ 开头的键。",
        "sections": {"scope": "", "overview": "", "underwriting": "", "benefits": "", "renewal_pricing": ""},
        "version_notes": {f"{v['old']['pid']}->{v['new']['pid']}": "" for v in cmp.get("versions", [])},
        "innovations": {pid: [{"feature": c["feature"], "label": c["label"], "_候选判定": c["verdict"],
                               "_本系列新增": c.get("new_in_series"), "_对照样本": f"{c['peers_with']}/{c['peers_total']} 款具备",
                               "_全网核验": describe(c.get("market") or {}),
                               "_提示": "；".join(c.get("flags", [])), "verdict": "", "summary": "", "risk_logic": "", "boundary": ""}
                              for c in info["candidates"] if needs_judgment(c)]
                        for pid, info in (inn.get("products") or {}).items()},
        "findings": [{"_说明": "多产品横比的核心发现（可选）；finding 一句话观点，detail 写具体产品与变化，logic 写风控逻辑与产品启示，boundary 写结论边界",
                      "finding": "", "detail": "", "logic": "", "boundary": ""}],
        "overall": {"summary": "", "trends": [], "risks": []},
    }
    old = read_json(ws.analysis_path, None)
    if isinstance(old, dict):  # 已有判断：只补缺少的键与条目，不覆盖已写内容
        for k, v in data.items():
            if k not in old:
                old[k] = v
            elif k == "sections":
                for sk, sv in v.items():
                    old[k].setdefault(sk, sv)
            elif k == "version_notes":
                for vk, vv in v.items():
                    old[k].setdefault(vk, vv)
            elif k == "innovations":
                for pid, lst in v.items():
                    have = {j.get("feature") for j in old[k].get(pid) or []}
                    old[k].setdefault(pid, [])
                    old[k][pid] += [j for j in lst if j["feature"] not in have]
            elif k == "overall":
                for ok_, ov in v.items():
                    old[k].setdefault(ok_, ov)
        data = old
    write_json(ws.analysis_path, data)
    return ws.analysis_path


def clean_patch_items(items: list[dict]) -> list[dict]:
    """去掉模板中的说明键与空项，便于直接 apply 模板文件。"""
    out = []
    for it in items:
        it = {k: v for k, v in it.items() if not k.startswith("_")}
        if not it.get("value") and it.get("status") not in ("待核", "不适用", "推定") and it.get("field") not in ("benefits", "rate_table"):
            continue
        out.append(it)
    return out


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=2)
