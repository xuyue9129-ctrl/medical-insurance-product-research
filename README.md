# 医疗险产品研究 Skill（medical-insurance-product-research）

一个遵循开放 [Agent Skills 规范](https://agentskills.io/specification) 的技能包，可在 Claude Code、OpenAI Codex、WorkBuddy / CodeBuddy、DeepSeek dsh、Devin、Cursor 等 Agent 中使用。它把医疗险产品研究做成一条**可复核**的流水线：

1. **模糊检索**：输入“蓝医保2027”“众民保中高端26”“长相安5号”“家安芯3.0 B款”这类简称，从内置约 200 款医疗险目录中给出候选、分数、匹配依据与同系列各版本；目录外产品生成联网检索词。
2. **检索、下载、归档**：条款、费率表、健康告知、责任免除/严重既往症清单、投保须知、产品说明书、医院/药品清单、服务手册……自动识别类型并按 `01_条款 / 02_费率表 / 03_健康告知 / 04_责任免除 / 05_投保须知 / 06_清单 / 07_服务手册` 命名，记录来源 URL、SHA256、页数、获取时间、证据等级（A/B/C/D）与版本核对结果。
3. **标准化产品形态**：54 个字段（公司与上市时间、适用人群定位（次标体/健康体）、健康告知（健告类型、问项原文、疾病问询范围、住院/检查问项与时限、可正常投保情形、告知效力）、核保准入、保障责任、续保机制、既往症与赔付规则、费率……），每个字段都有口径、示例与细节要求。**每个取值都挂逐字原文引文，写入时自动在原件文本中核验**，找不到就拒收。
4. **对比**：多产品横向对比、同系列新旧版本对比（含“免赔额提高/赔付比例提高”等候选方向判断）。
5. **创新点识别**：特征打标 + 市场基线（本次研究产品 + 可选的本机样本摘要目录）计算普及度、最早采用、是否本系列新增，由 Agent 按方法论判断真创新/差异化/跟进，并写明结论边界。
6. **对比表**：Excel 按样本自动选择版式——同系列版本对比（系列总览 + 责任覆盖速查 + 保费速览 + 版本对比）或多产品横比（产品总览 + 严重既往症对比 + 产品演进与趋势 + 核心发现），均含证据索引与资料清单，可点击打开原件。
7. **报告与 PPT（按需）**：对比表完成后询问用户是否需要；分析报告为 Markdown + Word（目录、分级标题、表题、三线表、页码，正文 [E#] 引用对应证据），汇报 PPT 为 16:9（章节目录、结论栏、对比表格、创新点卡片），可套用自有模板。

> 输出仅用于产品研究，不构成投保建议；保险责任以保险合同、投保页面及保险公司核保结论为准。

## 目录结构
```
medins-research-skill/
├── medical-insurance-product-research/   ← 技能本体（安装时复制这个目录）
│   ├── SKILL.md                           Agent 读取的说明（frontmatter + 工作流程 + 硬性规则）
│   ├── scripts/medins.py                  命令行入口（所有功能）
│   ├── scripts/medins_lib/                核心库（纯 Python）
│   ├── references/                        按需加载的参考：字段口径、资料来源、证据规则、创新点方法、兼容说明
│   └── assets/                            字段 schema、特征词典、产品目录与公司/域名目录
├── install.py                             跨 Agent 安装器
├── build_dist.py                          打包 zip（WorkBuddy 上传/分发）
├── requirements.txt
├── tests/                                 单元与集成测试（unittest，含虚构样例 PDF 与本地 HTTP 模拟站点）
└── tools/                                 种子目录生成、规范校验
```

## 安装
```bash
python -m pip install -r requirements.txt     # openpyxl + PDF 解析库（pymupdf/pdfplumber/pypdf）+ 可选 python-docx、python-pptx 等
python install.py --list                      # 查看检测到的 Agent
python install.py                             # 默认安装到 ~/.agents/skills，并为检测到的 Claude Code / WorkBuddy / CodeBuddy 各装一份
python install.py --check                     # 安装并用已安装副本自检
```
其他方式：
- 指定目标：`python install.py --targets agents,claude,workbuddy,codex,dsh,devin,cursor`
- 只装到某个项目：`python install.py --project <项目目录>`（写入 `.agents/skills`、`.claude/skills`、`.codebuddy/skills`）
- WorkBuddy 图形界面：`python build_dist.py` 生成 `dist/medical-insurance-product-research.zip`，在“技能 → 上传技能”导入
- 手动：把 `medical-insurance-product-research/` 整个目录复制到对应 Agent 的技能目录（见 `references/agent_compat.md`）
- 卸载：`python install.py --uninstall`

| Agent | 默认安装位置 | 调用 |
|---|---|---|
| Codex / DeepSeek dsh / Devin（通用目录） | `~/.agents/skills/` | 自动匹配，或 `$medical-insurance-product-research`（Codex）、`/medical-insurance-product-research`（Devin） |
| Claude Code | `~/.claude/skills/` | 自动匹配或 `/medical-insurance-product-research` |
| WorkBuddy / CodeBuddy | `~/.workbuddy/skills/`、`~/.codebuddy/skills/` | 自动匹配或技能面板 |

## 怎么用（对 Agent 说）
- “帮我研究蓝医保2027，和2025免健告版做版本对比，出Excel对比表。”（完成后 Agent 会问你要不要报告和 PPT）
- “整理众民保中高端2025、2026活动版、2026版三版的产品形态和既往症变化，标出原文依据。”
- “这个文件夹里是几款次标体产品的条款和费率表，按统一字段整理并找出各自的创新点。”

**引导模式**：循环运行 `medins.py next -w <工作区>`，执行它给出的命令；逐字段整理时 `medins.py task` 会给出字段口径、示例、检索命令、候选原文并生成补丁模板。按提示逐步完成即可，所有取值仍经过原文核验。

Agent 会按 SKILL.md 的流程执行：`resolve` → `register` → 联网检索/`discover`/`fetch` 或 `import` → `extract` → 阅读原文并用 `record apply` 逐字段写入（附引文）→ `validate` → `compare` → `innovate` → 撰写对比判断（`analysis.json`）→ `build` 出对比表 → 询问用户是否需要报告/PPT（`deliver`）→ `report` / `ppt`。

也可以直接在终端使用（`<SKILL>` = 技能目录）：
```bash
python <SKILL>/scripts/medins.py doctor
python <SKILL>/scripts/medins.py init -w ./研究 --title "蓝医保版本研究"
python <SKILL>/scripts/medins.py resolve "蓝医保2027"
python <SKILL>/scripts/medins.py register -w ./研究 --from-catalog p6cc294b9
python <SKILL>/scripts/medins.py import -w ./研究 --pid p6cc294b9 "<本地资料目录>"
python <SKILL>/scripts/medins.py extract -w ./研究 --apply-safe
python <SKILL>/scripts/medins.py text -w ./研究 --pid p6cc294b9 --grep "免赔额"
python <SKILL>/scripts/medins.py record apply -w ./研究 --pid p6cc294b9 --file patch.json
python <SKILL>/scripts/medins.py build -w ./研究                      # 对比表（--mode series|market 可强制版式）
python <SKILL>/scripts/medins.py deliver -w ./研究 --report yes --ppt yes
python <SKILL>/scripts/medins.py report -w ./研究                     # 分析报告（Markdown + Word）
python <SKILL>/scripts/medins.py ppt -w ./研究 [--template 模板.pptx] # 汇报 PPT
```
完整命令与补丁格式见 `medical-insurance-product-research/references/workflow.md`。

## 准确性与可溯源设计
- **原文引文强制核验**：引文须在归档原件的抽取文本中逐字（或 ≥0.90 相似度，处理 PDF 脚注编号/断行）找到，否则拒绝写入；`validate` 每次重新核验全部引文。
- **数值一致性检查**：取值里的金额、比例、天数必须能在引文中找到（含“万/千/百分之/中文数字”换算），否则警告。
- **状态分级**：原文核验 / 规则抽取（待确认）/ 推定（必须写依据）/ 待核（必须写原因）/ 不适用；Excel 中用不同标记与颜色区分。
- **版本身份核对**：登记备案条款名后，自动检查每份文件是否属于本版本；同一文件多来源按 SHA256 去重并保留全部来源。
- **证据等级**：按来源域名自动建议 A（保险公司/持牌中介/行业协会正式原件）、B（官方页面）、C（第三方/媒体）、D（无链接/研究者摘录），仅 C/D 支撑的结论会被标注。
- **费率表逐行搬运**：自动识别年龄档分块（含跨页续表），按确认的列写入，每一行都以原文行为引文，避免手抄错误。
- **统一证据编号**：Excel“证据索引”、报告附录与 PPT 页脚使用同一套 E# 编号，可回溯到文件、页码、来源链接与哈希。

## 测试
```bash
python -m unittest discover -s tests -v
```
覆盖：文本规范化与数值换算、模糊检索（含年份/款别/系列冲突）、资料分类、引文定位（断行/脚注/跨页/省略号）、费率表解析（跨页续表）、完整流水线（导入→抽取→写入核验→费率→缺口→校验→版本对比→创新点→两种版式 Excel→报告→PPT）、引导流程（对比判断、询问报告与 PPT）、本地 HTTP 模拟站点下载（链接发现、HTML 冒充 PDF、404）、CLI、技能规范与安装器。

## 维护
- 更新内置目录：`python tools/build_seed_catalog.py <研究总表.xlsx> --as-of YYYY-MM-DD`（工作表与列名约定见脚本顶部说明）；公开版只写产品名、公司、系列与官网链接，`--exclude` 可排除指定产品
- 本机市场基线（不入库）：加 `--private-out ~/.medins-research/catalog.json`，把含研究摘要的完整目录写到本机，创新点识别会自动用作对照样本
- 新增字段：编辑 `assets/schema/fields.json`；新增创新特征：编辑 `assets/schema/features.json`
- PPT 默认模板与署名（本机）：在 `~/.medins-research/config.json` 写 `{"ppt_template": "<模板.pptx>", "org": "<署名>"}`
- 新增公司官网域名：编辑 `assets/catalog/companies.json`（用于检索词与证据等级建议）
- 规范校验：`python tools/skill_spec.py`

## 许可
MIT（见 LICENSE）。内置目录仅含公开的产品名称、公司、系列与保险公司官网链接，不含个人研究摘要。
