"""下载、校验、分类与归档。

每份资料归档时都会：
1. 计算 SHA256（同一文件来自多个 URL 只存一份，来源全部记录）
2. 嗅探真实类型（服务器返回 HTML 冒充 PDF 时会识别出来）
3. 抽取按页文本写入 text/<pid>/，扫描件标记 scanned
4. 自动识别资料类型并按 01_条款 / 02_费率表 … 命名
5. 版本核对：正文是否出现已登记的备案条款名 / 产品名关键词 / 版本年份
6. 依来源域名建议证据等级（A/B/C/D），写入 00_manifest.json 与 00_资料清单.txt
"""
from __future__ import annotations

import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .catalog import load_companies
from .common import (MedinsError, load_asset_json, now_iso, read_json, safe_filename, sha256_file, today,
                     write_json, write_text)
from .doctypes import CORE_TYPES, DOC_TYPES, classify, classify_name, filename_from_url, type_name
from .normalize import match_key, name_key, nfkc, years_in
from .textextract import extract_pages, pages_to_text, pdf_tables
from .workspace import Workspace

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MAX_BYTES = 150 * 1024 * 1024
EVIDENCE_LEVELS = {
    "A": "保险公司/持牌中介/行业协会正式信息披露原件（条款、费率表、投保须知、清单等）",
    "B": "官方域名页面（销售页、发布稿、官方问答），非正式合同文件或未逐项复核",
    "C": "第三方渠道/媒体/测评文章",
    "D": "无链接线索（截图、口述、公众号标题等），仅作待核线索",
}
GAP_STATUSES = ["无健康告知（非缺文件）", "动态投保页核验说明（非逐字原件）", "未找到可确认文件", "待取得原件", "不适用"]


# ---------------- 网络 ----------------

def http_get(url: str, dest: Path, referer: str | None = None, timeout: int = 40, retries: int = 3,
             insecure: bool = False) -> dict:
    """下载到 dest，返回 {status, final_url, content_type, disposition_name, bytes}。失败抛 MedinsError。"""
    headers = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            try:
                import requests  # type: ignore
                with requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True,
                                  verify=not insecure) as r:
                    if r.status_code >= 400:
                        raise MedinsError(f"HTTP {r.status_code}")
                    size = 0
                    with open(dest, "wb") as f:
                        for chunk in r.iter_content(1 << 16):
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise MedinsError("文件超过 150MB 上限")
                            f.write(chunk)
                    return {"status": r.status_code, "final_url": r.url, "content_type": r.headers.get("Content-Type", ""),
                            "disposition_name": _disposition_name(r.headers.get("Content-Disposition", "")), "bytes": size}
            except ImportError:
                import ssl
                import urllib.request
                ctx = ssl._create_unverified_context() if insecure else None
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                    size = 0
                    with open(dest, "wb") as f:
                        while True:
                            chunk = r.read(1 << 16)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise MedinsError("文件超过 150MB 上限")
                            f.write(chunk)
                    return {"status": getattr(r, "status", 200), "final_url": r.geturl(),
                            "content_type": r.headers.get("Content-Type", ""),
                            "disposition_name": _disposition_name(r.headers.get("Content-Disposition", "")), "bytes": size}
        except MedinsError as e:
            last_err = e
            if "HTTP 4" in str(e):
                break
        except Exception as e:  # 网络/SSL 错误
            last_err = e
            if "CERTIFICATE" in str(e).upper() and not insecure:
                last_err = MedinsError(f"SSL 证书校验失败：{e}；确认是官方站点后可加 --insecure 重试")
                break
        time.sleep(1.5 * attempt)
    raise MedinsError(f"下载失败：{url}（{last_err}）")


def _disposition_name(cd: str) -> str:
    if not cd:
        return ""
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    if not m:
        return ""
    from urllib.parse import unquote
    name = unquote(m.group(1).strip())
    try:  # 部分服务器用 latin-1 传 UTF-8 文件名
        name = name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return name


def sniff(path: Path) -> str:
    """按文件头判断真实扩展名。"""
    with open(path, "rb") as f:
        head = f.read(2048)
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"PK\x03\x04"):
        import zipfile
        try:
            names = zipfile.ZipFile(path).namelist()
        except zipfile.BadZipFile:
            return ".zip"
        if any(n.startswith("word/") for n in names):
            return ".docx"
        if any(n.startswith("xl/") for n in names):
            return ".xlsx"
        return ".zip"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return ".doc"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    low = head.lstrip().lower()
    if low.startswith((b"<!doctype html", b"<html", b"<head", b"<meta", b"<!--")) or b"<html" in low[:600]:
        return ".html"
    if low.startswith((b"{", b"[")):
        return ".json"
    return ".txt"


# ---------------- 证据等级建议 ----------------

def suggest_evidence_level(url: str, doc_type: str, company: str = "", ext: str = ".pdf") -> tuple[str, str]:
    if not url:
        return "D", "本地文件，无来源链接"
    host = (urlparse(url).hostname or "").lower()

    def hit(domains) -> bool:
        return any(host == d or host.endswith("." + d) for d in domains)

    formal = doc_type in ("01", "02", "03", "04", "05", "06", "07") and ext in (".pdf", ".docx", ".xlsx", ".doc")
    companies = load_companies()
    own = [c for c in companies if name_key(c.get("short", "")) == name_key(company) or name_key(c.get("full", "")) == name_key(company)]
    if own and hit(own[0].get("domains", [])):
        return ("A", "本公司官方域名正式文件") if formal else ("B", "本公司官方域名页面")
    if any(hit(c.get("domains", [])) for c in companies):
        return ("B", "其他保险公司/集团官方域名，需核对是否为本产品") if formal else ("B", "保险集团官方域名页面")
    src = load_asset_json("catalog/source_domains.json", {}) or {}
    if any(hit(x["domains"]) for x in src.get("regulators", [])):
        return "A", "行业协会/监管/交易所披露"
    if any(hit(x["domains"]) for x in src.get("platforms", [])):
        return ("A", "持牌中介平台的正式附件") if formal else ("C", "持牌中介平台页面")
    if any(hit(x["domains"]) for x in src.get("media", [])):
        return "C", "媒体/测评"
    return "C", "第三方来源（域名未登记）"


# ---------------- 归档 ----------------

def manifest_path(ws: Workspace, pid: str) -> Path:
    return ws.product_dir(pid) / "00_manifest.json"


def load_manifest(ws: Workspace, pid: str) -> dict:
    p = ws.get(pid)
    m = read_json(manifest_path(ws, pid)) or {}
    m.setdefault("schema", "medins-archive/1")
    m["product"] = {k: p.get(k) for k in ("pid", "company", "insurer_full", "product_name", "series", "version",
                                          "clause_names", "access_channel", "body_type", "launch")}
    m.setdefault("documents", [])
    m.setdefault("gaps", [])
    return m


def save_manifest(ws: Workspace, pid: str, m: dict) -> None:
    m["updated_at"] = now_iso()
    m["documents"].sort(key=lambda d: (d.get("doc_type", "09"), d.get("file", "")))
    write_json(manifest_path(ws, pid), m)
    write_inventory(ws, pid, m)


def _guess_title(pages: list[str], fallback: str) -> str:
    keys = re.compile(r"(条款|费率表|费率|投保须知|说明书|健康告知|告知书|清单|目录|手册|指南|责任免除|严重疾病|既往症|名单|列表)")
    for page in pages[:2]:
        for line in page.splitlines()[:25]:
            s = re.sub(r"\s+", "", line)
            if 4 <= len(s) <= 60 and keys.search(s) and not re.search(r"[…\.．]{3,}|^\d+[.、]", s):
                return s
    return fallback


def detect_clause_names(pages: list[str]) -> list[str]:
    """从条款首页识别备案条款名（如“太保互联网个人一年期住院H款医疗保险”）。"""
    out = []
    for page in pages[:3]:
        for line in page.splitlines()[:40]:
            s = re.sub(r"\s+", "", nfkc(line))
            m = re.match(r"^(.{4,40}?(?:医疗保险|保险))(?:条款)$", s)
            if m and m.group(1) not in out:
                out.append(m.group(1))
    return out


def detect_clause_codes(pages: list[str]) -> list[str]:
    text = re.sub(r"[ \t]+", "", nfkc("\n".join(pages[:3])))
    pats = [r"[\u4e00-\u9fa5]{2,12}\[\d{4}\][\u4e00-\u9fa5]{0,10}\d{1,4}号",
            r"[\u4e00-\u9fa5]{1,8}[〔\[]\d{4}[〕\]]\d{1,4}号",
            r"C\d{20,26}"]
    out = []
    for p in pats:
        for m in re.findall(p, text):
            if m not in out:
                out.append(m)
    return out[:6]


def version_check(text: str, product: dict, doc_type: str) -> dict:
    """核对文件是否属于本产品/本版本，结果供人工确认，不自动拒收。"""
    t = match_key(text)
    hits, misses = [], []
    for cn in product.get("clause_names") or []:
        (hits if match_key(cn) in t else misses).append(f"条款名:{cn}")
    name = nfkc(product.get("product_name", ""))
    segs = [s for s in re.split(r"[·（）()\[\]【】\s/、，,]+", name) if len(s) >= 2]
    seg_hits = [s for s in segs if match_key(s) in t]
    hits += [f"名称片段:{s}" for s in seg_hits]
    py = years_in(product.get("product_name", "") + " " + str(product.get("version") or ""))
    doc_years = set(re.findall(r"(?<!\d)(20[1-3]\d)(?!\d)", nfkc(text)))
    year_note = ""
    if py and doc_years and not (py & doc_years):
        year_note = f"产品版本年份{'/'.join(sorted(py))}未出现在文件中（文件出现{'/'.join(sorted(doc_years)[:5])}）"
    if any(h.startswith("条款名") for h in hits):
        status = "matched"
    elif seg_hits and not year_note:
        status = "partial"
    else:
        status = "unverified"
    advice = ""
    if status != "matched":
        advice = ("未出现已登记的备案条款名：条款/费率表通常只写备案名而非销售名，请确认后用 "
                  "`register --clause-name` 登记备案条款名再运行 `verify-docs`；不能确认时不要用于取值。")
    return {"status": status, "hits": hits[:8], "misses": misses[:4], "year_note": year_note, "advice": advice}


def archive_file(ws: Workspace, pid: str, src: Path, source: dict | None = None, doc_type: str | None = None,
                 title: str | None = None, evidence_level: str | None = None, note: str = "", move: bool = False) -> dict:
    """把一个本地文件（下载的临时文件或用户提供的文件）归档到产品目录。返回文档条目。"""
    src = Path(src)
    if not src.exists() or src.stat().st_size == 0:
        raise MedinsError(f"文件不存在或为空：{src}")
    product = ws.get(pid)
    pid = product["pid"]
    m = load_manifest(ws, pid)
    source = dict(source or {})
    source.setdefault("retrieved_at", now_iso())
    digest = sha256_file(src)
    for d in m["documents"]:
        if d.get("sha256") == digest:
            if source.get("url") and source["url"] not in [s.get("url") for s in d.get("sources", [])]:
                d.setdefault("sources", []).append(source)
                save_manifest(ws, pid, m)
            if move:
                src.unlink(missing_ok=True)
            return {**d, "duplicate": True}

    real_ext = sniff(src)
    ext = real_ext if real_ext != ".txt" else (src.suffix.lower() or ".txt")
    tmp_named = None
    if src.suffix.lower() != ext:  # 让抽取器按真实类型解析
        tmp_named = Path(tempfile.mkdtemp(prefix="medins_")) / f"f{ext}"
        shutil.copy2(src, tmp_named)
    parse_path = tmp_named or src
    pages, backend, scanned, warn = [], "", False, ""
    if ext in (".pdf", ".html", ".htm", ".docx", ".xlsx", ".txt", ".json", ".md", ".csv"):
        try:
            info = extract_pages(parse_path)
            pages, backend, scanned = info["pages"], info["backend"], info["scanned"]
        except MedinsError as e:
            warn = str(e)
    first_text = "\n".join(pages[:2])
    anchor = source.get("anchor", "")
    url = source.get("url", "")
    if doc_type:
        code, basis = doc_type, "人工指定"
    else:
        code, basis = classify(anchor=anchor, url=url, title=title or src.stem, first_pages_text=first_text)
    if code not in DOC_TYPES:
        raise MedinsError(f"资料类型编号无效：{code}（可选 {', '.join(DOC_TYPES)}）")
    if code == "02" and ext == ".pdf" and pages:  # 费率表按行重建，保证“年龄档 + 各列费率”在同一行
        try:
            info = extract_pages(parse_path, layout=True)
            pages, backend = info["pages"], info["backend"]
        except MedinsError:
            pass
    fallback = title or anchor or source.get("disposition_name") or filename_from_url(url).rsplit(".", 1)[0] or src.stem
    fallback = re.sub(r"^\d{2}_", "", fallback)
    best_title = title or _guess_title(pages, fallback)
    best_title = re.sub(r"\.(pdf|html?|docx?|xlsx?)$", "", best_title, flags=re.I)
    pdir = ws.product_dir(pid)
    fname = safe_filename(f"{code}_{best_title}{ext}", 90)
    target, i = pdir / fname, 2
    while target.exists():
        target = pdir / safe_filename(f"{code}_{best_title}_{i}{ext}", 90)
        i += 1
    if move:
        shutil.move(str(src), str(target))
    else:
        shutil.copy2(src, target)
    if tmp_named:
        shutil.rmtree(tmp_named.parent, ignore_errors=True)

    text_file = ""
    if pages:
        tdir = ws.text_dir(pid)
        tpath = tdir / f"{target.name}.txt"
        write_text(tpath, pages_to_text(pages))
        text_file = tpath.relative_to(ws.root).as_posix()
        if code == "02" and ext == ".pdf":
            tables = pdf_tables(target)
            if tables:
                write_json(tdir / f"{target.name}.tables.json", tables)
    level, level_basis = suggest_evidence_level(url, code, product.get("company", ""), ext)
    if evidence_level:
        level, level_basis = evidence_level.upper(), "人工指定"
    vc = version_check("\n".join(pages), product, code) if pages else {"status": "unverified", "hits": [], "advice": "无可抽取文本"}
    entry = {
        "file": target.name, "doc_type": code, "doc_type_name": type_name(code), "classified_by": basis,
        "title": best_title, "ext": ext, "sha256": digest, "bytes": target.stat().st_size,
        "pages": len(pages) if ext == ".pdf" else None, "text_backend": backend, "text_file": text_file,
        "scanned": scanned, "sources": [source] if source else [], "evidence_level": level,
        "evidence_basis": level_basis, "version_check": vc, "note": note, "archived_at": now_iso(),
    }
    if code == "01" and pages:
        entry["detected_clause_names"] = detect_clause_names(pages)
        entry["detected_clause_codes"] = detect_clause_codes(pages)
    if warn:
        entry["warning"] = warn
    if scanned:
        entry["warning"] = (entry.get("warning", "") + "；疑似扫描件：文本极少，需 OCR 或人工逐字核对").strip("；")
    if ext == ".html" and pages and len(re.sub(r"\s", "", pages[0])) < 200:
        entry["warning"] = "页面正文极少，可能为动态渲染：请用浏览器打开核对或截图后以 note 记录"
    m["documents"].append(entry)
    m["gaps"] = [g for g in m["gaps"] if g.get("doc_type") != code or g.get("status") == "不适用"]
    save_manifest(ws, pid, m)
    return entry


def fetch_url(ws: Workspace, pid: str, url: str, anchor: str = "", doc_type: str | None = None, title: str | None = None,
              referer: str | None = None, evidence_level: str | None = None, insecure: bool = False, note: str = "") -> dict:
    tmpdir = Path(tempfile.mkdtemp(prefix="medins_dl_"))
    try:
        tmp = tmpdir / "download.bin"
        info = http_get(url, tmp, referer=referer, insecure=insecure)
        source = {"url": url, "final_url": info["final_url"], "content_type": info["content_type"],
                  "http_status": info["status"], "anchor": anchor, "disposition_name": info.get("disposition_name", ""),
                  "via": "download", "referer": referer or "", "retrieved_at": now_iso()}
        return archive_file(ws, pid, tmp, source=source, doc_type=doc_type, title=title or info.get("disposition_name") or None,
                            evidence_level=evidence_level, note=note, move=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def recover_sources(folder: Path) -> dict[str, dict]:
    """从既有归档目录的来源索引（00_来源索引*.json / 00_*说明.txt / *.url）恢复“文件名 → 来源”对应。"""
    out: dict[str, dict] = {}
    for jf in folder.glob("*来源索引*.json"):
        data = read_json(jf, []) or []
        if isinstance(data, dict):
            data = data.get("files") or data.get("documents") or list(data.values())
        for it in data if isinstance(data, list) else []:
            if isinstance(it, dict) and (it.get("name") or it.get("file")) and it.get("url"):
                out[Path(str(it.get("name") or it.get("file"))).name] = {
                    "url": it["url"], "retrieved_at": it.get("核验日期") or it.get("retrieved_at") or "",
                    "via": "recovered:" + jf.name, "anchor": it.get("source", "")}
    for tf in list(folder.glob("00_*.txt")):
        try:
            lines = [ln.strip() for ln in tf.read_text(encoding="utf-8-sig", errors="replace").splitlines()]
        except OSError:
            continue
        for i, ln in enumerate(lines):
            if re.match(r"^\d{2}_.+\.(pdf|html?|docx?|xlsx?)$", ln, re.I):
                for nxt in lines[i + 1:i + 5]:
                    mm = re.search(r"https?://\S+", nxt)
                    if mm:
                        out.setdefault(ln, {"url": mm.group(0), "via": "recovered:" + tf.name})
                        break
    for uf in folder.glob("*.url"):
        txt = uf.read_text(encoding="utf-8", errors="replace")
        mm = re.search(r"URL=(\S+)", txt)
        if mm:
            out[uf.name] = {"url": mm.group(1), "via": "recovered:" + uf.name, "anchor": uf.stem}
    return out


def import_folder(ws: Workspace, pid: str, folder: Path, evidence_level: str | None = None,
                  include_txt_notes: bool = True) -> list[dict]:
    """导入既有资料目录（保持原件不动，复制归档）。自动恢复来源链接。"""
    folder = Path(folder)
    if not folder.is_dir():
        raise MedinsError(f"目录不存在：{folder}")
    sources = recover_sources(folder)
    results = []
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.name.startswith(("~$", ".")) or f.suffix.lower() in (".url", ".lnk", ".tmp"):
            continue
        if f.name.startswith("00_") or "来源索引" in f.name:
            continue  # 旧清单不当作资料
        if f.suffix.lower() in (".txt", ".md") and not include_txt_notes:
            continue
        title = re.sub(r"^\d{2}_", "", f.stem)
        mm = re.match(r"^(0[1-9])_", f.name)
        named = classify_name(title)
        code = named or (mm.group(1) if mm else None)  # 以文件名语义为准，旧编号仅兜底
        if f.suffix.lower() in (".txt", ".md"):
            status = next((s for s in GAP_STATUSES if s.split("（")[0] in f.name), None)
            if status or re.search(r"未找到|待取得|核验说明|非缺文件", f.name):
                note = f.read_text(encoding="utf-8-sig", errors="replace").strip()[:800]
                results.append({"file": f.name, "gap": mark_gap(ws, pid, code or "09", status or "未找到可确认文件",
                                                                note=f"（导入自既有说明文件 {f.name}）{note}")})
                continue
        src = dict(sources.get(f.name, {}))
        src.setdefault("via", "local-import")
        src["local_path"] = str(f)
        extra_note = "研究者整理/摘录文本，非保险公司原件" if f.suffix.lower() in (".txt", ".md") else ""
        level = evidence_level or ("D" if extra_note else None)
        try:
            results.append(archive_file(ws, pid, f, source=src, doc_type=None if named else code, title=title,
                                        evidence_level=level, note=extra_note))
        except MedinsError as e:
            results.append({"file": f.name, "error": str(e)})
    return results


def mark_gap(ws: Workspace, pid: str, doc_type: str, status: str, note: str = "", evidence: str = "") -> dict:
    """登记资料缺口或“非缺文件”说明（如免健告产品没有健康告知），并写占位说明文件。"""
    if doc_type not in DOC_TYPES:
        raise MedinsError(f"资料类型编号无效：{doc_type}")
    if status not in GAP_STATUSES:
        raise MedinsError(f"状态须为：{' / '.join(GAP_STATUSES)}")
    product = ws.get(pid)
    pid = product["pid"]
    m = load_manifest(ws, pid)
    fname = safe_filename(f"{doc_type}_{type_name(doc_type).split('/')[0]}_{status}.txt", 90)
    body = [f"产品：{product['product_name']}", f"公司：{product.get('insurer_full') or product['company']}",
            f"资料类别：{doc_type} {type_name(doc_type)}", f"状态：{status}", f"核验日期：{today()}"]
    if note:
        body.append(f"说明：{note}")
    if evidence:
        body.append(f"依据：{evidence}")
    body.append("提醒：本文件是缺口/口径说明，不是保险公司原件；实际投保以投保页面与保险合同为准。")
    write_text(ws.product_dir(pid) / fname, "\n".join(body) + "\n")
    m["gaps"] = [g for g in m["gaps"] if g.get("doc_type") != doc_type] + [
        {"doc_type": doc_type, "doc_type_name": type_name(doc_type), "status": status, "note": note,
         "evidence": evidence, "file": fname, "marked_at": now_iso()}]
    save_manifest(ws, pid, m)
    return m["gaps"][-1]


def completeness(m: dict) -> dict:
    have = {d["doc_type"] for d in m.get("documents", [])}
    gaps = {g["doc_type"]: g["status"] for g in m.get("gaps", [])}
    out = {}
    for code in CORE_TYPES:
        if code in have:
            out[code] = "已归档"
        elif code in gaps:
            out[code] = gaps[code]
        else:
            out[code] = "缺失（未登记原因）"
    return out


def write_inventory(ws: Workspace, pid: str, m: dict | None = None) -> Path:
    """生成人可读的 00_资料清单.txt（沿用既有归档格式）。"""
    m = m or load_manifest(ws, pid)
    p = ws.get(pid)
    lines = [f"公司：{p.get('insurer_full') or p['company']}", f"产品：{p['product_name']}"]
    if p.get("clause_names"):
        lines.append("备案条款：" + "；".join(p["clause_names"]))
    if p.get("access_channel"):
        lines.append(f"准入通道：{p['access_channel']}")
    lines.append(f"整理日期：{today()}")
    lines += ["", "文件分类："]
    by_type: dict[str, list[dict]] = {}
    for d in m["documents"]:
        by_type.setdefault(d["doc_type"], []).append(d)
    for code, nm in DOC_TYPES.items():
        docs = by_type.get(code, [])
        gap = next((g for g in m["gaps"] if g["doc_type"] == code), None)
        if not docs and not gap:
            continue
        lines.append(f"- {nm}：")
        for d in docs:
            meta = [f"证据{d.get('evidence_level', '?')}"]
            if d.get("pages"):
                meta.append(f"{d['pages']}页")
            meta.append(f"SHA256 {d['sha256'][:16]}…")
            vc = d.get("version_check", {}).get("status")
            if vc and vc != "matched":
                meta.append(f"版本核对:{vc}")
            if d.get("warning"):
                meta.append("⚠" + d["warning"][:40])
            lines.append(f"  - {d['file']}（{'，'.join(meta)}）")
        if gap:
            lines.append(f"  - 【{gap['status']}】{gap.get('note', '')}")
    lines += ["", "核心资料完整度："]
    for code, st in completeness(m).items():
        lines.append(f"- {code} {DOC_TYPES[code]}：{st}")
    urls = []
    for d in m["documents"]:
        for s in d.get("sources", []):
            if s.get("url"):
                urls.append(f"- {d['file']} ← {s['url']}")
    if urls:
        lines += ["", "来源/核验线索："] + urls
    warns = [f"- {d['file']}：{d['version_check'].get('year_note') or d['version_check'].get('advice')}"
             for d in m["documents"] if d.get("version_check", {}).get("status") not in ("matched", None)]
    if warns:
        lines += ["", "版本与完整性提醒："] + warns
    lines += ["", "说明：文件按公开披露页面、持牌销售页面及研究者提供的本地资料整理；营销名称与备案条款名称可能不同。",
              "本整理仅用于资料归档，不替代实际投保页面、保险合同、特别约定及保险公司核保结论。"]
    path = ws.product_dir(pid) / "00_资料清单.txt"
    write_text(path, "\n".join(lines) + "\n")
    return path


def reverify_documents(ws: Workspace, pid: str) -> list[dict]:
    """登记了备案条款名后重新做版本核对。"""
    from .common import read_text
    product = ws.get(pid)
    m = load_manifest(ws, pid)
    out = []
    for d in m["documents"]:
        if d.get("text_file"):
            text = read_text(ws.root / d["text_file"])
            d["version_check"] = version_check(text, product, d["doc_type"])
        out.append({"file": d["file"], "version_check": d.get("version_check", {}).get("status")})
    save_manifest(ws, pid, m)
    return out
