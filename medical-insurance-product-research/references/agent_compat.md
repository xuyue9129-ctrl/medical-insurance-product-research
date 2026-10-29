# 跨 Agent 兼容说明

本技能遵循开放 Agent Skills 规范（https://agentskills.io/specification）：目录名 = `name`（小写短横线），`SKILL.md` 含 YAML frontmatter（name、description、license、compatibility、metadata），脚本放 `scripts/`，参考资料放 `references/`，数据与模板放 `assets/`。脚本是纯 Python 标准命令行，不依赖任何 Agent 专有工具。

## 安装位置
用仓库根目录的 `install.py` 自动安装（复制，非链接）：
```bash
python install.py --list                 # 查看检测到的 Agent 与目标目录
python install.py                        # 自动：~/.agents/skills + 检测到的 Claude/WorkBuddy/CodeBuddy 等
python install.py --targets claude,codex,workbuddy,dsh,devin,cursor
python install.py --project <项目目录>    # 只装到项目：.agents/skills、.claude/skills、.codebuddy/skills
python install.py --uninstall
```

| Agent | 用户级目录 | 项目级目录 | 调用方式 |
|---|---|---|---|
| 通用（Agent Skills 规范） | `~/.agents/skills/` | `.agents/skills/` | 由 Agent 按 description 自动选用 |
| OpenAI Codex | `~/.agents/skills/`（旧：`~/.codex/skills/`） | `.agents/skills/` | `$medical-insurance-product-research` 或 `/skills` |
| Claude Code | `~/.claude/skills/` | `.claude/skills/` | `/medical-insurance-product-research` 或自动 |
| WorkBuddy / CodeBuddy | `~/.workbuddy/skills/`、`~/.codebuddy/skills/`；或在“技能—上传技能”导入 zip | `.codebuddy/skills/` | 对话中自动或 `/技能名` |
| DeepSeek dsh | `~/.dsh/skills/`、`~/.agents/skills/` | `.dsh/skills/`、`.agents/skills/` | 自动或技能面板 |
| Devin | `~/.agents/skills/`、`~/.config/devin/skills/`（Windows：`%APPDATA%\devin\skills\`） | `.devin/skills/`、`.agents/skills/` | `/medical-insurance-product-research` 或自动 |
| Cursor | `~/.cursor/skills/` | `.cursor/skills/` | 自动 |

同一 Agent 若同时扫描多个目录（如 Codex 读 `~/.agents/skills` 与 `~/.codex/skills`），只装一处即可，避免列表中出现两份。`install.py` 默认只装 `~/.agents/skills` 加上不读该目录的 Agent（Claude Code、WorkBuddy/CodeBuddy）。

## 打包分发
```bash
python build_dist.py          # 生成 dist/medical-insurance-product-research.zip（zip 根目录即技能目录，含 manifest.yaml）
```
zip 可直接用于 WorkBuddy“上传技能”、Claude.ai 技能上传，或发给他人解压到上述目录。

## 运行要求
- Python 3.8+；`pip install -r requirements.txt`（openpyxl、pymupdf/pdfplumber/pypdf、python-docx、requests、beautifulsoup4）
- Windows / macOS / Linux 均可；路径、文件名自动规避非法字符与保留名，输出统一 UTF-8。
- 没有联网工具的 Agent：可只用本地导入完成全部流程。
