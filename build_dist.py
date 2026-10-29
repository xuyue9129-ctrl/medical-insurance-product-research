#!/usr/bin/env python3
"""打包分发：生成 dist/<技能名>.zip，zip 根目录即技能目录（含 manifest.yaml，兼容 WorkBuddy 上传）。"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL = "medical-insurance-product-research"
SRC = ROOT / SKILL
sys.path.insert(0, str(ROOT / "tools"))

from skill_spec import parse_frontmatter, validate_skill  # noqa: E402

SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIX = {".pyc", ".tmp"}


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    problems = validate_skill(SRC)
    if problems:
        print("规范校验未通过：\n" + "\n".join(problems))
        return 1
    fm, _ = parse_frontmatter((SRC / "SKILL.md").read_text(encoding="utf-8"))
    manifest = (f"name: {SKILL}\n"
                f"description: \"医疗险产品研究：模糊检索、资料归档、字段标准化（原文引文核验）、版本对比、创新点识别、Excel与报告\"\n"
                f"category: insurance-research\nauthor: {(fm.get('metadata') or {}).get('author', '')}\nlicense: {fm.get('license', '')}\n")
    out = ROOT / "dist" / f"{SKILL}.zip"
    out.parent.mkdir(exist_ok=True)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(SRC.rglob("*")):
            if f.is_dir() or SKIP_DIRS & set(f.parts) or f.suffix in SKIP_SUFFIX:
                continue
            z.write(f, f"{SKILL}/{f.relative_to(SRC).as_posix()}")
            n += 1
        z.writestr(f"{SKILL}/manifest.yaml", manifest)
        lic = ROOT / "LICENSE"
        if lic.exists():
            z.write(lic, f"{SKILL}/LICENSE")
    print(f"✔ {out}（{n + 1} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
