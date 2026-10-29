"""按开放 Agent Skills 规范校验技能目录（无第三方依赖，供 build_dist.py 与测试复用）。

规则：https://agentskills.io/specification
- SKILL.md 以 YAML frontmatter 开头；name 1–64 位小写字母/数字/短横线，不以短横线开头或结尾、无连续短横线，且与目录名一致
- description 1–1024 字符；compatibility ≤500 字符；metadata 为字符串到字符串的映射
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise ValueError("SKILL.md 必须以 YAML frontmatter（---）开头")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("frontmatter 缺少结束的 ---")
    raw, body = text[3:end].strip("\n"), text[end + 4:]
    data: dict = {}
    cur_map = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            if cur_map is None:
                raise ValueError(f"无法解析的缩进行：{line}")
            k, _, v = line.strip().partition(":")
            data[cur_map][k.strip()] = _scalar(v)
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        if v.strip() == "":
            data[k] = {}
            cur_map = k
        else:
            data[k] = _scalar(v)
            cur_map = None
    try:  # 有 PyYAML 时再做一次严格解析
        import yaml  # type: ignore
        strict = yaml.safe_load(raw)
        if isinstance(strict, dict):
            data = strict
    except ImportError:
        pass
    return data, body


def _scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def validate_skill(skill_dir: Path) -> list[str]:
    skill_dir = Path(skill_dir)
    errs = []
    md = skill_dir / "SKILL.md"
    if not md.exists():
        return [f"缺少 {md}"]
    try:
        fm, body = parse_frontmatter(md.read_text(encoding="utf-8"))
    except ValueError as e:
        return [str(e)]
    name = str(fm.get("name", ""))
    if not (1 <= len(name) <= 64) or not NAME_RE.match(name):
        errs.append(f"name 不合规：{name!r}")
    if name != skill_dir.name:
        errs.append(f"name（{name}）须与目录名（{skill_dir.name}）一致")
    desc = str(fm.get("description", ""))
    if not (1 <= len(desc) <= 1024):
        errs.append(f"description 长度须为 1–1024，当前 {len(desc)}")
    comp = fm.get("compatibility")
    if comp is not None and not (1 <= len(str(comp)) <= 500):
        errs.append(f"compatibility 长度须为 1–500，当前 {len(str(comp))}")
    meta = fm.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in meta.items()):
            errs.append("metadata 须为字符串到字符串的映射（版本号请加引号）")
    allowed = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    extra = set(fm) - allowed
    if extra:
        errs.append(f"frontmatter 含规范外字段：{sorted(extra)}")
    if len(body.splitlines()) > 500:
        errs.append("SKILL.md 正文建议不超过 500 行")
    for ref in re.findall(r"\]\(([^)#]+)\)", body):
        if not ref.startswith("http") and not (skill_dir / ref).exists():
            errs.append(f"SKILL.md 引用的文件不存在：{ref}")
    return errs


def skill_version(skill_dir: Path) -> str:
    fm, _ = parse_frontmatter((Path(skill_dir) / "SKILL.md").read_text(encoding="utf-8"))
    return str((fm.get("metadata") or {}).get("version", "0.0.0"))


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):  # Windows 默认控制台编码（cp1252/GBK）无法输出 ✔ 与中文
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    target = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "medical-insurance-product-research")
    problems = validate_skill(target)
    print("\n".join(problems) if problems else f"✔ {target.name} 符合 Agent Skills 规范")
    sys.exit(1 if problems else 0)
