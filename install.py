#!/usr/bin/env python3
"""跨 Agent 安装器：把技能目录复制到各 Agent 的技能目录（Windows / macOS / Linux）。

  python install.py --list                    查看可用目标与检测结果
  python install.py                           自动安装（~/.agents/skills + 检测到的 Claude Code / WorkBuddy / CodeBuddy）
  python install.py --targets claude,codex     指定目标（agents, claude, codex, workbuddy, codebuddy, dsh, devin, cursor）
  python install.py --project <项目目录>       安装到项目级目录（.agents/.claude/.codebuddy）
  python install.py --uninstall [--targets …]  卸载
  python install.py --dry-run                  只显示将执行的操作
已存在同名技能时会先备份到 ~/.medins-research/backups/（不放在技能目录里，避免被当成第二个技能）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL = "medical-insurance-product-research"
SRC = ROOT / SKILL
HOME = Path.home()


def devin_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming")) / "devin" / "skills"
    return Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "devin" / "skills"


TARGETS = {
    "agents": (HOME / ".agents" / "skills", "通用目录：Codex、DeepSeek dsh、Devin 等读取", None),
    "claude": (HOME / ".claude" / "skills", "Claude Code", HOME / ".claude"),
    "codex": (HOME / ".codex" / "skills", "Codex 旧目录（已读 ~/.agents/skills 时不必再装）", HOME / ".codex"),
    "workbuddy": (HOME / ".workbuddy" / "skills", "WorkBuddy", HOME / ".workbuddy"),
    "codebuddy": (HOME / ".codebuddy" / "skills", "CodeBuddy / WorkBuddy Enterprise", HOME / ".codebuddy"),
    "dsh": (HOME / ".dsh" / "skills", "DeepSeek dsh 专用目录（已读 ~/.agents/skills 时不必再装）", HOME / ".dsh"),
    "devin": (devin_dir(), "Devin 全局目录（已读 ~/.agents/skills 时不必再装）", None),
    "cursor": (HOME / ".cursor" / "skills", "Cursor", HOME / ".cursor"),
}
AUTO = ["agents", "claude", "workbuddy", "codebuddy"]
PROJECT_DIRS = [".agents/skills", ".claude/skills", ".codebuddy/skills"]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".DS_Store", "*.tmp")


def detected(key: str) -> bool:
    marker = TARGETS[key][2]
    return marker is None or marker.exists()


def resolve_targets(args) -> list[tuple[str, Path]]:
    if args.project:
        base = Path(args.project).expanduser().resolve()
        return [(f"project:{d}", base / d) for d in PROJECT_DIRS]
    if args.targets:
        keys = [k.strip() for k in args.targets.split(",") if k.strip()]
        bad = [k for k in keys if k not in TARGETS]
        if bad:
            sys.exit(f"未知目标：{bad}；可选 {list(TARGETS)}")
    else:
        keys = [k for k in AUTO if detected(k)]
    return [(k, TARGETS[k][0]) for k in keys]


def install_one(dest_root: Path, dry: bool) -> str:
    dest = dest_root / SKILL
    if dry:
        return f"[dry-run] 复制 {SRC} → {dest}"
    dest_root.mkdir(parents=True, exist_ok=True)
    note = ""
    if dest.exists() or dest.is_symlink():
        bdir = HOME / ".medins-research" / "backups" / f"{SKILL}-{dt.datetime.now():%Y%m%d%H%M%S}-{dest_root.parent.name.strip('.')}"
        bdir.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            dest.unlink()
        else:
            shutil.move(str(dest), str(bdir))
            note = f"（旧版已备份到 {bdir}）"
    shutil.copytree(SRC, dest, ignore=IGNORE)
    return f"✔ 已安装 {dest}{note}"


def uninstall_one(dest_root: Path, dry: bool) -> str:
    dest = dest_root / SKILL
    if not dest.exists():
        return f"- 未安装：{dest}"
    if dry:
        return f"[dry-run] 删除 {dest}"
    shutil.rmtree(dest)
    return f"✔ 已卸载 {dest}"


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="安装医疗险产品研究 Skill 到各 Agent")
    ap.add_argument("--targets", help="逗号分隔：" + ",".join(TARGETS))
    ap.add_argument("--project", help="安装到项目目录（.agents/.claude/.codebuddy 下的 skills）")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", action="store_true", help="安装后用已安装副本运行 doctor 与规范校验")
    args = ap.parse_args()
    if not (SRC / "SKILL.md").exists():
        sys.exit(f"找不到技能目录：{SRC}")
    sys.path.insert(0, str(ROOT / "tools"))
    from skill_spec import validate_skill
    problems = validate_skill(SRC)
    if problems:
        sys.exit("技能未通过规范校验：\n" + "\n".join(problems))
    if args.list:
        print(f"技能 {SKILL}")
        for k, (path, desc, _) in TARGETS.items():
            mark = "✔" if (path / SKILL).exists() else ("·" if detected(k) else " ")
            auto = "（默认）" if k in AUTO and detected(k) else ""
            print(f"  {mark} {k:<10} {path}  —— {desc}{auto}")
        print("  ✔=已安装  ·=检测到该 Agent")
        return 0
    targets = resolve_targets(args)
    if not targets:
        print("没有可安装的目标（可用 --targets 指定）")
        return 1
    for key, root in targets:
        msg = uninstall_one(root, args.dry_run) if args.uninstall else install_one(root, args.dry_run)
        print(f"[{key}] {msg}")
    if args.check and not args.uninstall and not args.dry_run:
        inst = targets[0][1] / SKILL
        print(f"\n用已安装副本自检：{inst}")
        rc = subprocess.call([sys.executable, str(inst / "scripts" / "medins.py"), "doctor"])
        errs = validate_skill(inst)
        print("规范校验：" + ("通过" if not errs else "；".join(errs)))
        return rc or (1 if errs else 0)
    if not args.uninstall and not args.dry_run:
        print("\n依赖：python -m pip install -r requirements.txt   自检：python install.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
