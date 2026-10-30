"""生成汇报 PPT（16:9）。

结构：封面 → 章节目录（当前章节高亮）→ 内容页 → 结尾页。内容页统一为“左上标题 + 标题下结论栏 + 表格/卡片 + 底部要点栏 + 右下页码”。
  1、样本概况与主要结论：样本产品一览
  2、核保准入与既往症：健康告知与核保准入、既往症与赔付规则
  3、保障形态与版本演进：基础形态与费率、保障责任、各版本变化
  4、创新点与风险提示：各产品创新点卡片、趋势判断与风险提示
表格取值来自标准化记录（已核验的原文），单元格内只放要点，证据编号与原文页码见对比表“证据索引”。
结论栏与要点栏取自 analysis.json（研究者判断）；未撰写时只放描述性标题，不替研究者下结论。
可用 --template 指定自有 .pptx 模板（按版式名匹配：标题幻灯片/目录幻灯片/仅标题/结尾幻灯片，或英文 Title Slide/Title Only），
也可在 ~/.medins-research/config.json 写 {"ppt_template": "...", "org": "..."} 作为默认值。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .citations import build_index, refs_for
from .innovation import needs_judgment
from .market import describe
from .common import MedinsError, read_json, today, truncate
from .records import field_map, load_record
from .report import BENEFIT_KEYS, CLAIM_KEYS, FORM_KEYS, TOKEN, UNDERWRITING_KEYS
from .workspace import Workspace

try:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.oxml.ns import qn
    from pptx.util import Inches, Pt
except ImportError as e:  # pragma: no cover
    raise MedinsError("生成 PPT 需要 python-pptx：pip install python-pptx") from e

DEEP, TITLE_INK, ACCENT, LIGHT, PALE, BAR, RED, INK, MUTED = "015B86", "333F50", "5B9BD5", "DEEBF7", "F5F9FD", "F2F2F2", "C00000", "262626", "7F7F7F"
FONT, TOC_FONT = "微软雅黑", "华文新魏"
CHAPTERS = ["样本概况与主要结论", "核保准入与既往症", "保障形态与版本演进", "创新点与风险提示"]
SOURCE = "注：取值均引自已归档的条款、费率表等原件；证据编号、原文页码与来源见对比表“证据索引”。"
SW = 13.333


def user_config() -> dict:
    home = Path(os.environ.get("MEDINS_HOME") or (Path.home() / ".medins-research"))
    return read_json(home / "config.json", {}) or {}


def _font(run, size=None, bold=None, color=None, name=FONT):
    f = run.font
    if size:
        f.size = Pt(size)
    if bold is not None:
        f.bold = bold
    if color:
        f.color.rgb = RGBColor.from_string(color)
    f.name = name
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", name)


def _runs(p, text, size, bold, color, name=FONT):
    for part in re.split(r"(\*\*[^*]+\*\*)", text or ""):
        if not part:
            continue
        b = part.startswith("**") and part.endswith("**")
        r = p.add_run()
        r.text = part[2:-2] if b else part
        _font(r, size, bold or b, color, name)


def _text(slide, x, y, w, h, lines, size=14, bold=False, color=INK, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, name=FONT,
          space=0):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.08)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    for i, ln in enumerate(lines if isinstance(lines, list) else [lines]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if space:
            p.space_after = Pt(space)
        if isinstance(ln, tuple):
            _runs(p, ln[0], ln[1].get("size", size), ln[1].get("bold", bold), ln[1].get("color", color), ln[1].get("name", name))
        else:
            _runs(p, ln, size, bold, color, name)
    return tb


def _rect(slide, x, y, w, h, fill, shape=MSO_SHAPE.RECTANGLE, grad=None):
    sh = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if grad:
        sh.fill.gradient()
        sh.fill.gradient_angle = grad[2] if len(grad) > 2 else 0
        st = sh.fill.gradient_stops
        st[0].color.rgb = RGBColor.from_string(grad[0])
        st[1].color.rgb = RGBColor.from_string(grad[1])
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = RGBColor.from_string(fill)
    sh.line.fill.background()
    sh.shadow.inherit = False
    return sh


def _table(slide, headers, rows, x, y, w, h, widths=None, size=10.5, head_size=12, first_bold=True):
    n_r, n_c = len(rows) + 1, len(headers)
    gt = slide.shapes.add_table(n_r, n_c, Inches(x), Inches(y), Inches(w), Inches(h))
    tbl = gt.table
    tblPr = tbl._tbl.tblPr
    tblPr.set("firstRow", "1")
    tblPr.set("bandRow", "0")
    widths = widths or [w / n_c] * n_c
    for i, cw in enumerate(widths):
        tbl.columns[i].width = Inches(cw)
    head_h = 0.42
    body_h = max(0.32, (h - head_h) / max(1, len(rows)))
    tbl.rows[0].height = Inches(head_h)
    for r in range(1, n_r):
        tbl.rows[r].height = Inches(body_h)
    for r, row in enumerate([headers] + rows):
        for c in range(n_c):
            cell = tbl.cell(r, c)
            cell.margin_left = cell.margin_right = Inches(0.06)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            if r == 0:
                cell.fill.fore_color.rgb = RGBColor.from_string(ACCENT)
            else:
                cell.fill.fore_color.rgb = RGBColor.from_string(LIGHT if r % 2 else PALE)
            tf = cell.text_frame
            tf.word_wrap = True
            txt = str(row[c] if c < len(row) else "")
            for i, ln in enumerate(txt.split("\n")):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.alignment = PP_ALIGN.CENTER if (r == 0 or (c == 0 and first_bold)) else PP_ALIGN.LEFT
                _runs(p, ln, head_size if r == 0 else size, r == 0 or (c == 0 and first_bold), "FFFFFF" if r == 0 else INK)
    return tbl


def _strip(text: str) -> str:
    return re.sub(r"\s+", " ", TOKEN.sub("", text or "")).strip()


def first_sentence(text: str, n: int = 90) -> str:
    t = _strip((text or "").split("\n")[0])
    m = re.match(r"^(.+?[。！？])", t)
    t = m.group(1) if m else t
    if len(t) <= n:
        return t
    cut = max(t.rfind(c, 0, n) for c in "，；：、")  # 过长时在最后一个逗号/分号处截断
    return (t[:cut] if cut > n // 2 else t[:n - 1]) + "…"


class Deck:
    def __init__(self, template: str | None = None):
        self.tpl = bool(template)
        if template:
            if not Path(template).exists():
                raise MedinsError(f"PPT 模板不存在：{template}")
            self.prs = Presentation(template)
            lst = self.prs.slides._sldIdLst
            for sid in list(lst):
                self.prs.part.drop_rel(sid.rId)
                lst.remove(sid)
        else:
            self.prs = Presentation()
            self.prs.slide_width, self.prs.slide_height = Inches(SW), Inches(7.5)
        self.n = 0

    def _layout(self, names, idx):
        for lay in self.prs.slide_layouts:
            if lay.name in names:
                return lay
        lays = self.prs.slide_layouts
        return lays[min(idx, len(lays) - 1)] if idx >= 0 else lays[len(lays) - 1]

    def new(self, kind: str):
        if self.tpl:
            lay = {"cover": self._layout(("标题幻灯片", "Title Slide"), 0),
                   "agenda": self._layout(("目录幻灯片", "Section Header", "节标题"), 2),
                   "content": self._layout(("仅标题", "Title Only"), 5),
                   "end": self._layout(("结尾幻灯片", "Closing", "结束页"), -1)}[kind]
        else:
            lay = self.prs.slide_layouts[6]
        self.n += 1
        return self.prs.slides.add_slide(lay)

    def _page(self, s):
        _text(s, 12.2, 7.12, 1.05, 0.3, f"第 {self.n} 页", 10, False, MUTED, PP_ALIGN.RIGHT)

    def _placeholder(self, s, types):
        for ph in s.placeholders:
            if ph.placeholder_format.type in types:
                return ph
        return None

    def cover(self, title: str, sub: str):
        from pptx.enum.shapes import PP_PLACEHOLDER as P
        s = self.new("cover")
        t = self._placeholder(s, (P.TITLE, P.CENTER_TITLE)) if self.tpl else None
        if t is not None:
            t.text = title
            st = self._placeholder(s, (P.SUBTITLE,))
            if st is not None:
                st.text = sub
            return s
        _rect(s, 0, 5.85, SW, 1.65, None, grad=(DEEP, TITLE_INK, 0))
        _rect(s, 1.43, 4.18, 1.6, 0.06, ACCENT)
        _text(s, 1.43, 1.6, 10.45, 2.5, title, 36, True, TITLE_INK, anchor=MSO_ANCHOR.BOTTOM)
        _text(s, 1.43, 4.35, 10.0, 0.9, sub, 18, False, "595959")
        return s

    def agenda(self, current: int):
        s = self.new("agenda")
        if not self.tpl:
            _rect(s, 0.85, 1.0, 0.09, 0.75, DEEP)
            _text(s, 1.03, 0.98, 4.0, 0.8, [("目　录", {"size": 32, "bold": True, "color": DEEP})], anchor=MSO_ANCHOR.MIDDLE)
            _rect(s, 8.63, 2.81, 4.7, 4.01, PALE)
            _text(s, 8.9, 3.0, 4.2, 1.9, f"{current + 1:02d}", 96, True, ACCENT, PP_ALIGN.LEFT, MSO_ANCHOR.BOTTOM)
            _rect(s, 9.0, 4.95, 3.6, 0.06, ACCENT)
            _text(s, 8.9, 5.1, 4.3, 0.8, CHAPTERS[current], 20, True, DEEP)
        lines = [(f"{i + 1}、{c}", {"color": RED if i == current else "404040"}) for i, c in enumerate(CHAPTERS)]
        _text(s, 1.6, 2.4, 7.0, 3.8, lines, 28, True, name=TOC_FONT, space=14)
        self._page(s)
        return s

    def content(self, title: str, message: str = "", takeaway: str = "", source: str = SOURCE):
        s = self.new("content")
        t = s.shapes.title if self.tpl else None
        if t is not None:
            t.text = title
        else:
            _text(s, 0.2, 0.1, 11.02, 0.59, truncate(title, 40), 26 if len(title) <= 24 else 22 if len(title) <= 30 else 18, True, TITLE_INK,
                  anchor=MSO_ANCHOR.BOTTOM)
            _rect(s, 0.2, 0.7, 11.02, 0.04, None, grad=("E8F1FA", DEEP, 0))
        top = 0.95
        if message:
            _rect(s, 0, 0.78, SW, 0.75, BAR)
            n = len(message)
            _text(s, 0.2, 0.8, SW - 0.4, 0.71, truncate(message, 110), 20 if n <= 40 else 18 if n <= 55 else 16 if n <= 72 else 14,
                  True, INK, anchor=MSO_ANCHOR.MIDDLE)
            top = 1.68
        bottom = 7.0
        if takeaway:
            _rect(s, 0.5, 6.1, SW - 1.0, 0.95, LIGHT)
            _text(s, 0.6, 6.12, SW - 1.2, 0.9, truncate(takeaway, 150), 14 if len(takeaway) <= 75 else 12, False, INK,
                  anchor=MSO_ANCHOR.MIDDLE)
            bottom = 5.98
        if source:
            _text(s, 0.2, 7.12, 11.6, 0.3, source, 9, False, MUTED)
        self._page(s)
        return s, top, bottom

    def end(self, line: str):
        s = self.new("end")
        if not self.tpl:
            _rect(s, 0, 0, SW, 7.5, None, grad=(DEEP, TITLE_INK, 315))
            _text(s, 0.78, 2.9, 8.0, 0.9, "谢　谢", 40, True, "FFFFFF", anchor=MSO_ANCHOR.BOTTOM)
            _rect(s, 0.85, 3.95, 4.93, 0.03, "FFFFFF")
            _text(s, 0.78, 4.1, 10.0, 0.6, line, 14, False, "FFFFFF")
        return s


def build_ppt(ws: Workspace, pids: list[str] | None = None, out: Path | None = None, title: str = "",
              template: str | None = None, org: str = "") -> dict:
    pids = [ws.get(p)["pid"] for p in (pids or ws.research_pids())]
    if not pids:
        raise MedinsError("工作区没有产品")
    cfg = user_config()
    template = template if template is not None else cfg.get("ppt_template")
    org = org or cfg.get("org", "")
    prods = [ws.get(p) for p in pids]
    recs = [load_record(ws, p) for p in pids]
    fm = field_map()
    _, idx = build_index(ws, pids)
    cmp = read_json(ws.outputs_dir / "对比结果.json", {}) or {}
    inn = read_json(ws.outputs_dir / "创新点候选.json", {}) or {}
    analysis = read_json(ws.analysis_path, {}) or {}
    if not isinstance(analysis, dict):
        analysis = {}
    secs, overall = analysis.get("sections") or {}, analysis.get("overall") or {}
    title = title or analysis.get("title") or (ws.load() or {}).get("title") or "医疗险产品研究"
    deck = Deck(template or None)
    warnings: list[str] = []

    def val(rec, key, n):
        f = rec["fields"].get(key, {})
        st = f.get("status", "未填")
        if st == "未填":
            return "—"
        if st in ("待核", "不适用"):
            return st
        v = truncate(re.sub(r"\s+", " ", f.get("value", "")), n)
        return v + ("〔推定〕" if st == "推定" else "〔待确认〕" if st == "规则抽取" else "〔待复核〕" if st == "待复核" else "")

    def short(p):
        return f"{p['company']}\n{truncate(p['product_name'], 22)}"

    def refs_line(*texts):
        ids = []
        for t in texts:
            for m in TOKEN.finditer(t or ""):
                ids += [x for x in refs_for(idx, m.group(1), m.group(2)) if x not in ids]
        return (SOURCE + (f"　本页结论依据：{'、'.join(ids[:8])}" if ids else "")) if ids else SOURCE

    def cmp_slides(name, keys, message, takeaway="", raw=""):
        n = len(prods)
        if n <= 4:
            per, lim = 6, {1: 150, 2: 95, 3: 62, 4: 46}[n]
            chunks = [keys[i:i + per] for i in range(0, len(keys), per)]
            for ci, ch in enumerate(chunks):
                s, top, bottom = deck.content(name + ("（续）" if ci else ""), message if ci == 0 else "",
                                              takeaway if ci == len(chunks) - 1 else "", refs_line(raw) if ci == 0 else SOURCE)
                first = 1.9
                rows = [[fm[k]["label"]] + [val(r, k, lim) for r in recs] for k in ch]
                _table(s, ["维度"] + [short(p) for p in prods], rows, 0.2, top + 0.08, SW - 0.4, bottom - top - 0.16,
                       [first] + [(SW - 0.4 - first) / n] * n, 11 if n <= 2 else 10)
        else:
            per_k, per_p = 4, 7
            for ki in range(0, len(keys), per_k):
                for pi in range(0, n, per_p):
                    ks, ps = keys[ki:ki + per_k], list(range(pi, min(n, pi + per_p)))
                    first_slide = ki == 0 and pi == 0
                    s, top, bottom = deck.content(name + ("" if first_slide else "（续）"), message if first_slide else "", "",
                                                  refs_line(raw) if first_slide else SOURCE)
                    rows = [[prods[i]["company"] + "·" + truncate(prods[i]["product_name"], 16)] + [val(recs[i], k, 40) for k in ks] for i in ps]
                    first = 2.6
                    _table(s, ["产品"] + [fm[k]["label"] for k in ks], rows, 0.2, top + 0.08, SW - 0.4, bottom - top - 0.16,
                           [first] + [(SW - 0.4 - first) / len(ks)] * len(ks), 9.5)

    sub = "　".join(x for x in [org, "产品研究", today()] if x)
    deck.cover(title, sub)

    # 1 样本概况与主要结论
    deck.agenda(0)
    summary = overall.get("summary") or ""
    msg = first_sentence(summary) or f"本次研究共 {len(prods)} 款产品，覆盖 {len({p['company'] for p in prods})} 家保险公司"
    heads = ["公司", "产品", "适用人群定位", "健告类型", "上市时间", "保险期间 / 保证续保", "50岁保费（元/年）"]
    for pi in range(0, len(prods), 7):
        s, top, bottom = deck.content("样本产品一览" + ("（续）" if pi else ""), msg if pi == 0 else "",
                                      first_sentence(secs.get("scope", ""), 140) if pi == 0 else "", refs_line(summary) if pi == 0 else SOURCE)
        rows = [[p["company"], p["product_name"], val(r, "body_type", 30), val(r, "access_channel", 24), val(r, "launch_date", 16),
                 f"{val(r, 'term', 14)} / {val(r, 'guaranteed_renewal', 18)}", val(r, "premium_50m", 36)]
                for p, r in list(zip(prods, recs))[pi:pi + 7]]
        _table(s, heads, rows, 0.2, top + 0.08, SW - 0.4, min(bottom - top - 0.16, 0.42 + 0.62 * len(rows)),
               [1.4, 2.6, 2.0, 1.6, 1.2, 2.0, 2.13], 10.5)
    if summary and len(_strip(summary)) > len(msg) + 10:
        s, top, bottom = deck.content("主要结论", "", "", refs_line(summary))
        paras = [p for p in (_strip(x) for x in summary.split("\n")) if p]
        _text(s, 0.5, top + 0.2, SW - 1.0, bottom - top - 0.4, [f"{i}. {truncate(p, 160)}" for i, p in enumerate(paras[:6], 1)], 16, space=10)

    # 2 核保准入与既往症
    deck.agenda(1)
    u = secs.get("underwriting", "")
    cmp_slides("核保端：健康告知与核保准入", UNDERWRITING_KEYS,
               first_sentence(u) or "对比健康告知类型、问询范围、可正常投保情形与核保辅助手段", raw=u)
    cmp_slides("理赔端：既往症与赔付规则", CLAIM_KEYS, "对比既往症定义、一般/严重既往症处理与首年规则",
               "健告题数减少不等于准入放宽；免健告产品的风险管控更多体现在严重既往症除外与首年规则上。")

    # 3 保障形态与版本演进
    deck.agenda(2)
    o_txt = (secs.get("overview", "") + "\n" + secs.get("renewal_pricing", "")).strip()
    cmp_slides("基础形态与费率", FORM_KEYS, first_sentence(o_txt) or "对比保险期间、续保、免赔额、赔付比例与 50 岁保费",
               "保费口径默认 46–50 岁、有社保、首年、年交、含等待期；保险期间不等于保证续保期间。", raw=o_txt)
    b_txt = secs.get("benefits", "")
    cmp_slides("保障责任", BENEFIT_KEYS, first_sentence(b_txt) or "对比一般医疗、重疾医疗、门急诊、康复、外购药与先进疗法责任", raw=b_txt)
    notes = analysis.get("version_notes") or {}
    for v in cmp.get("versions", []):
        o, nw = v["old"], v["new"]
        note = notes.get(f"{o['pid']}->{nw['pid']}") or notes.get(f"{o['product_name']}->{nw['product_name']}") or ""
        note = note if isinstance(note, str) else note.get("summary", "")
        prio = {"既往症与赔付规则": 0, "核心额度与保障责任": 1, "特殊责任与健康服务": 1, "投保与核保准入": 2, "保费": 2, "续保机制": 3}
        chs = sorted(v["changes"], key=lambda c: (0 if c.get("direction") else 1 if c["change"] != "变化" else 2, prio.get(c.get("section"), 4)))
        shown = chs[:14]
        for ci in range(0, max(1, len(shown)), 7):
            more = f"另有 {len(chs) - len(shown)} 个维度的变化见对比表“版本对比”。" if len(chs) > len(shown) and ci + 7 >= len(shown) else ""
            s, top, bottom = deck.content(f"版本演进：{truncate(o['product_name'], 14)} → {truncate(nw['product_name'], 14)}" + ("（续）" if ci else ""),
                                          (first_sentence(note) or f"{o.get('launch') or '旧版'} → {nw.get('launch') or '新版'}，共 {len(chs)} 个维度发生变化") if ci == 0 else "",
                                          "", (refs_line(note) if ci == 0 else SOURCE) + ("　" + more if more else ""))
            rows = [[c["label"], truncate(c["old"] or "—", 70), truncate(c["new"] or "—", 70), c.get("direction") or c["change"]] for c in shown[ci:ci + 7]]
            if rows:
                _table(s, ["维度", "旧版", "新版", "变化（方向为候选判断）"], rows, 0.2, top + 0.08, SW - 0.4, bottom - top - 0.16,
                       [1.9, 4.35, 4.35, 2.33], 10)
            else:
                _text(s, 0.5, top + 0.3, 10, 0.6, "两版标准化字段未见差异。", 16)

    # 4 创新点与风险提示
    deck.agenda(3)
    judg = analysis.get("innovations") or {}
    any_inn = False
    for pid, info in (inn.get("products") or {}).items():
        jl = [j for j in (judg.get(pid) or []) if isinstance(j, dict) and (j.get("summary") or "").strip()]
        cards = [(j.get("label") or j.get("feature"), j.get("verdict", ""), _strip(j.get("summary", "")), _strip(j.get("boundary", "")))
                 for j in jl]
        judged = bool(cards)
        if not judged:
            cards = [(c["label"], f"候选：{c['verdict']}", truncate(c.get("product_snippet", ""), 90),
                      describe(c.get("market") or {}) + "；" + "；".join(c.get("flags", [])))
                     for c in info["candidates"] if needs_judgment(c)]
        if not cards:
            continue
        any_inn = True
        strong = sum(1 for c in cards if str(c[1]).startswith(("真创新", "差异化")))
        msg = (f"研究者认定 {strong} 项真创新/差异化特征（样本内比较）" if judged else f"脚本识别 {len(cards)} 项候选特征，待研究者判断")
        for ci in range(0, len(cards), 3):
            s, top, bottom = deck.content(f"创新点识别：{truncate(info['product'], 26)}" + ("（续）" if ci else ""), msg if ci == 0 else "", "",
                                          refs_line(*[j.get("summary", "") for j in jl]) if ci == 0 else SOURCE)
            group = cards[ci:ci + 3]
            w = (SW - 0.4 - 0.25 * (len(group) - 1)) / len(group)
            for k, (lab, verdict, summ, bound) in enumerate(group):
                x = 0.2 + k * (w + 0.25)
                _rect(s, x, top + 0.15, w, 0.75, DEEP)
                _text(s, x + 0.05, top + 0.17, w - 0.1, 0.71, [(truncate(lab, 26), {"size": 16, "bold": True, "color": "FFFFFF"}),
                                                              (verdict, {"size": 12, "bold": True, "color": "FFD966"})], anchor=MSO_ANCHOR.MIDDLE)
                _rect(s, x, top + 0.9, w, bottom - top - 1.05, PALE)
                _text(s, x + 0.08, top + 1.0, w - 0.16, bottom - top - 1.9, truncate(summ, 170) or "—", 13, False, INK)
                if bound:
                    _text(s, x + 0.08, bottom - 0.85, w - 0.16, 0.7, "结论边界：" + truncate(bound, 70), 10, False, MUTED)
    if not any_inn:
        s, top, bottom = deck.content("创新点识别", "未发现样本内少见或本系列新增的特征")
    trends = [_strip(t) for t in overall.get("trends") or [] if (t or "").strip()]
    risks = [_strip(t) for t in overall.get("risks") or [] if (t or "").strip()]
    gaps = []
    for p, r in zip(prods, recs):
        pend = [fm[k]["label"] for k, f in r["fields"].items() if f.get("status") == "待核"]
        if pend:
            gaps.append(f"{truncate(p['product_name'], 16)}：{'、'.join(pend[:4])}{'等' if len(pend) > 4 else ''}")
    s, top, bottom = deck.content("趋势判断与风险提示", first_sentence(trends[0], 90) if trends else "",
                                  ("待核事项：" + "；".join(gaps[:3])) if gaps else "",
                                  refs_line(*(overall.get("trends") or []), *(overall.get("risks") or [])))
    w = (SW - 0.4 - 0.3) / 2
    for k, (lab, lst) in enumerate((("趋势判断", trends), ("风险提示", risks))):
        x = 0.2 + k * (w + 0.3)
        _rect(s, x, top + 0.1, w, 0.5, DEEP if k == 0 else RED)
        _text(s, x + 0.1, top + 0.1, w - 0.2, 0.5, lab, 16, True, "FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
        _rect(s, x, top + 0.6, w, bottom - top - 0.7, PALE)
        items = [f"{i}. {truncate(t, 95)}" for i, t in enumerate(lst[:5], 1)] or ["（尚未撰写：analysis.json → overall）"]
        _text(s, x + 0.1, top + 0.7, w - 0.2, bottom - top - 0.9, items, 13, False, INK if lst else MUTED, space=8)
    if not trends and not risks:
        warnings.append("analysis.json 的 overall.trends / overall.risks 为空，PPT 末页只有占位提示")

    deck.end("数据与原文依据见对比表“证据索引”")
    out = out or (ws.outputs_dir / f"{title}_汇报_{today().replace('-', '')}.pptx")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        deck.prs.save(str(out))
    except PermissionError:
        out = out.with_name(out.stem + "_new" + out.suffix)
        deck.prs.save(str(out))
    return {"pptx": str(out), "slides": deck.n, "template": template or "", "warnings": warnings}
