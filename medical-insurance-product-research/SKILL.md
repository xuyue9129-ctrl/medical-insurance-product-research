---
name: medical-insurance-product-research
description: 医疗险产品研究：按（可模糊的）产品名称检索、下载并归档条款、费率表、健康告知、责任免除/既往症清单、投保须知、医院与药品清单等全部资料；按统一字段抽取并标准化产品形态——公司与上市时间、适用人群定位（面向次标体还是健康体）、健康告知（健告类型、问项原文、疾病问询范围、住院/检查问项与时限、可正常投保情形、告知效力）、核保准入、保障责任、续保机制、既往症与赔付规则、费率，每个取值附原文引文、页码与来源并自动核验；做多产品与新旧版本对比，结合市场同类样本识别真正的创新点，输出 Excel 对比表，并可按需生成分析报告（Word）与汇报 PPT。Use for Chinese health/medical insurance (百万医疗、中高端医疗、长期医疗、免健告/简易健告/次标体) product research, clause & rate-table archiving, version comparison, innovation analysis.
license: MIT
compatibility: 需要 Python 3.8+ 与 openpyxl，PDF 解析需 pymupdf/pdfplumber/pypdf 任一，Word 报告与 PPT 分别需 python-docx、python-pptx；联网检索优先用 Agent 自带搜索/浏览工具，无联网时可导入本地资料。适用于 Claude Code、Codex、WorkBuddy/CodeBuddy、DeepSeek dsh、Devin、Cursor 等支持 Agent Skills 的工具。
metadata:
  author: medins-research
  language: zh-CN
---

# 医疗险产品研究（检索 · 归档 · 标准化 · 对比 · 创新点 · 对比表 · 报告与PPT）

本技能把“查资料—归档—逐字段整理—对比—判断创新—出对比表—（按需）报告与PPT”做成可复核的流水线：**脚本负责确定性工作**（模糊检索、下载、哈希、分类、抽取文本、引文核验、对比、出表），**你（Agent）负责阅读原文与判断**，并且每个判断都必须挂上可核验的原文引文。

下文 `<SKILL_DIR>` 指本 SKILL.md 所在目录；命令统一写作 `python <SKILL_DIR>/scripts/medins.py …`（macOS/Linux 可用 `python3`，Windows 可用 `py -3`）。所有子命令支持 `-h` 与 `--json`。

## 引导模式（所有 Agent 都按这个循环做）
不要自己规划步骤，脚本会告诉你下一步：
```bash
python <SKILL_DIR>/scripts/medins.py next -w <工作区>     # ① 看当前阶段与下一步命令
# ② 执行它给出的命令（把 <...> 换成真实内容；需要向用户确认的先问用户）
# ③ 再运行 next，循环，直到阶段为“完成”
```
对比表生成后，`next` 会进入“询问报告与PPT”：**必须先问用户“是否需要制作对应的分析报告（Word）和汇报 PPT？”**，按回答运行 `deliver --report yes|no --ppt yes|no`，不要替用户决定。
逐字段整理阶段，`next` 会让你运行 `task`：它给出该字段的口径、示例、去哪找、检索命令、候选原文，并生成补丁模板文件。你只需：
1. 运行任务卡里的检索命令，读命中的原文上下文；
2. 编辑补丁文件：`value` 按口径写全（数值+条件+例外），`quote` 从检索输出**逐字复制**；找不到就改成 `{"field": "...", "status": "待核", "note": "查过哪些文件、为何无法确认"}`；
3. 运行任务卡里的 `record apply` 命令；看到 ✘ 就按提示改，看到 ⚠ 要复核；
4. 回到 `next`。

想批量处理时用 `template --pid <pid>` 生成全部待填字段的骨架（已预填候选引文），填完一次性 `record apply`。

| 常见错误 | 正确做法 |
|---|---|
| 引文自己改写或概括 | 从 `text --grep` 输出中原样复制一段连续文字 |
| 取值只写“有”“可赔”“见条款” | 写数值和条件：“医保内/外各1万免赔；计划一至四赔付50%/70%/80%/100%” |
| 找不到就凭经验填 | 标“待核”并写原因 |
| 把旧版本条款用于新版本 | 先 `register --clause-name` 登记备案条款名，`verify-docs` 显示 matched 才用 |
| validate 有 error 就出报告 | error 清零后再 `build` |
| 对比表做完就直接生成报告/PPT，或直接结束 | 先问用户要不要报告和 PPT，再 `deliver` 登记 |

## 何时使用
- 用户给出一个或多个医疗险产品名（可能是简称、错别字、只写年份或系列，如“蓝医保2027”“众民保中高端26”“长相安5号”），要求查条款/费率/健告、整理产品形态、做对比、看版本变化或创新点、出 Excel/报告。
- 用户提供了本地已下载的条款/费率表文件夹，需要标准化整理和对比。

不适用：给个人做投保建议或核保结论（本技能只做产品研究，输出需注明“以保险合同和投保页面为准”）。

## 硬性规则（准确性与可溯源）
1. **原文优先**：取值只能来自已归档的原件（条款、费率表、投保须知、健告、清单等）。营销稿、测评、媒体只能作线索（证据 C/D 级），不能单独支撑结论。
2. **每个非空取值都要有逐字引文**：用 `record apply` 写入时脚本会在抽取文本中核验引文；找不到就拒绝写入。不要改写引文、不要拼接不相邻的句子（可用“……”连接同页的两段原文）。
3. **不知道就写“待核”并说明原因**（如“健告逐题原文未公开”），不得用经验或同系列旧版本补齐。归纳/推断用“推定”并在 note 写依据。
4. **版本身份必须核对**：销售名≠备案条款名。登记备案条款名（`register --clause-name`），确认条款、费率表、清单属于目标版本；旧版文件不得冒用到新版本。
5. **口径写清**：保险期间≠保证续保；健告题数减少≠准入放宽；上市时间写依据（“最迟某日已上线”照实写，附件配置时间不是首发日）；保费默认 46–50 岁档、有社保、首年、年交、含等待期，组合产品主险与必选附加险分列。
6. **validate 的 error 必须清零**才能把相关内容写进结论；warning 要逐条看过。
7. 不覆盖用户已有文件：工作区单独建目录；导入本地资料是复制，原件不动。

## 工作流程

### 0. 环境与范围
```bash
python <SKILL_DIR>/scripts/medins.py doctor          # 缺库时按提示 pip install
python <SKILL_DIR>/scripts/medins.py init -w <工作区> --title "<研究标题>"
```
先和用户确认：研究哪些产品（含哪些版本）、是否有本地资料、输出放哪。工作区建议放在用户的项目目录下（如 `<项目>/medins_workspace`）。

### 1. 模糊检索并登记产品
```bash
python <SKILL_DIR>/scripts/medins.py resolve "蓝医保2027"
python <SKILL_DIR>/scripts/medins.py register -w <工作区> --from-catalog <id>
# 目录里没有：先联网确认官方名称，再
python <SKILL_DIR>/scripts/medins.py register -w <工作区> --company 众安在线 --product "众民保·中高端医疗2026" --series 众民保·中高端 --add-to-catalog
```
`resolve` 会给出候选分数、匹配依据、同系列各版本。显示“有歧义”时（同系列不同年份/计划/A·B款），向用户确认或把各版本都纳入版本对比。

### 2. 收集并归档资料
1. 生成检索词并用**你自己的联网搜索/浏览工具**执行：`medins.py queries "<产品名>" --company <公司>`。优先找保险公司官网“公开信息披露/互联网保险产品信息”、持牌中介销售页附件、保险行业协会产品库（入口见 [references/sources.md](references/sources.md)）。没有联网工具时可用兜底 `medins.py search`。
2. 找到产品页后列出附件：`medins.py discover <页面URL或保存的HTML> --product "<产品名>" --save links.json`
3. 下载归档：`medins.py fetch -w <工作区> --pid <pid> <URL>...`，或 `--from-discover links.json --types 01,02,03,04,05,06,07`
4. 本地资料：`medins.py import -w <工作区> --pid <pid> <文件或目录>`（自动恢复既有“来源索引”里的链接）
5. 登记备案条款名并复核版本：`register -w <工作区> --pid <pid> --clause-name "<主险条款名>" --clause-name "<附加险条款名>"` → `verify-docs`
6. 取不到的核心资料必须登记：`medins.py gap -w <工作区> --pid <pid> --type 03 --status "无健康告知（非缺文件）" --note "官方投保页：无需健康告知" --evidence "<URL>"`（状态可选：无健康告知（非缺文件）/动态投保页核验说明（非逐字原件）/未找到可确认文件/待取得原件/不适用）

健告常在动态投保页展示：用浏览器工具打开投保流程逐字抄录（保存为 .html/.txt 后 `import`，证据等级按来源标 B/D），或登记为“动态投保页核验说明”。

### 3. 抽取、阅读、逐字段写入
```bash
python <SKILL_DIR>/scripts/medins.py extract -w <工作区> --apply-safe      # 写入公司全称、条款名、编号、投保年龄、期间、续保、等待期（状态“规则抽取”）
python <SKILL_DIR>/scripts/medins.py fields                                # 字段口径（* 为必填）
python <SKILL_DIR>/scripts/medins.py drafts -w <工作区> --pid <pid> [--field deductible] [--hints]
python <SKILL_DIR>/scripts/medins.py text -w <工作区> --pid <pid> --grep "免赔额|赔付比例"   # 带页码检索原文
python <SKILL_DIR>/scripts/medins.py text -w <工作区> --pid <pid> --doc 01_ --page 9        # 读整页
```
逐个字段阅读原文后，写一个补丁 JSON 再 `record apply`（一次可写多个字段）：
```json
[
  {"field": "deductible", "value": "一般医疗与可选门急诊共用年免赔额1.5万元",
   "evidence": [{"doc": "01_太保互联网个人一年期住院H款医疗保险条款.pdf", "page": 3, "quote": "<逐字原文>"}]},
  {"field": "hd_text", "status": "待核", "note": "健告在动态投保页展示，本次未取得逐字原文"},
  {"field": "positioning", "status": "推定", "value": "<归纳>", "note": "<依据>", "evidence": [ ... ]},
  {"field": "benefits", "rows": [{"name": "一般医疗保险金", "plan": "计划一", "limit": "300万元", "deductible": "1.5万元", "ratio": "100%", "scope": "住院、特殊门诊、门诊手术、住院前后门急诊", "evidence": [ ... ]}]}
]
```
```bash
python <SKILL_DIR>/scripts/medins.py record apply -w <工作区> --pid <pid> --file patch.json
python <SKILL_DIR>/scripts/medins.py rates list -w <工作区> --pid <pid>             # 费率表自动分块（含跨页续表）
python <SKILL_DIR>/scripts/medins.py rates import -w <工作区> --pid <pid> --block 1 --columns "计划一,计划二" --col-index 0,1 --basis "有社保·年交·含等待期·首次投保"
python <SKILL_DIR>/scripts/medins.py record show -w <工作区> --pid <pid> --missing
```
字段与详细程度参照 [references/field_guide.md](references/field_guide.md)：每格写到“能直接用于对比”的程度——数值+条件+例外（如“医保内/外各1万免赔；计划一/二/三/四赔付50%/70%/80%/100%；有社保未经社保结算按60%”），结节三高要分投保端与理赔端，既往症要写清清单出处与阈值。

### 4. 校验
```bash
python <SKILL_DIR>/scripts/medins.py validate -w <工作区>
```
逐条处理 error（引文找不到、必填未处理、待复核、推定无依据、核心资料缺失未登记）；warning 中“规则抽取待确认”要读原文确认后用 `record apply` 重写为原文核验。

### 5. 对比与创新点
```bash
python <SKILL_DIR>/scripts/medins.py compare -w <工作区> [--pair 旧pid:新pid]   # 同系列自动按上市时间配对
python <SKILL_DIR>/scripts/medins.py innovate -w <工作区>
```
`innovate` 只给“候选”（样本内首见/少见差异化/行业常见、是否本系列新增、同业示例）。你要按 [references/innovation_method.md](references/innovation_method.md) 判断是否**真正的创新**（改变了谁能买、赔什么、怎么定价/分层，而不是换个名字或跟进常见责任）。`template --analysis` 生成 `<工作区>/analysis.json` 骨架，先写**对比判断**：
```json
{
  "version_notes": {"<旧pid>-><新pid>": "主险由F款换为H款[[<新pid>:clause_names]]；免赔额…[[<新pid>:deductible]]"},
  "innovations": {"<pid>": [{"feature": "pre_first_year_option", "label": "一般既往症首年比例可选", "verdict": "真创新/差异化/跟进/营销口径",
                              "summary": "…[[<pid>:preexisting_general]]", "risk_logic": "…", "boundary": "样本内首见，不代表全市场"}]},
  "findings": [{"finding": "一句话核心发现", "detail": "具体产品与变化[[<pid>:字段]]", "logic": "风控逻辑与产品启示", "boundary": "结论边界"}]
}
```
用 `[[pid:字段]]` 引用证据，输出时自动换成 [E#]；含数字的判断句没有引用会被自检列出。`findings` 用于多产品横比，可选。

### 6. 对比表
```bash
python <SKILL_DIR>/scripts/medins.py build -w <工作区>     # = validate → compare → innovate → excel；--mode series|market 可强制版式
```
对比表版式按样本自动选择：
- **同系列版本对比**（series）：说明、①系列总览（维度×版本，含责任覆盖速查 ✔/✘/◐ 与保费速览，差异维度浅青底）、②保障责任明细（按计划展开）、③保费对比（46–50 岁档高亮）、④健告与既往症、⑤版本对比、⑥创新点识别、⑦证据索引、⑧资料清单、⑨校验结果。
- **多产品横比**（market）：说明、①产品总览（每行一款，按“产品基本信息 / 投保与健康告知 / 既往症与理赔 / 核心额度与特殊责任 / 保费”分组，冻结前三列，可筛选）、②严重既往症对比（含病种速查）、③产品演进与趋势、④核心发现、⑤维度对比、⑥保障责任明细、⑦保费对比、⑧健告与既往症、⑨创新点识别、⑩证据索引、⑪资料清单、⑫校验结果。

### 7. 询问并生成报告与 PPT
对比表完成后**询问用户是否需要分析报告（Word）和汇报 PPT**，按回答登记：
```bash
python <SKILL_DIR>/scripts/medins.py deliver -w <工作区> --report yes --ppt no
```
需要任一项时，先在 analysis.json 写报告正文：`sections.scope / underwriting / overview / renewal_pricing / benefits`（各段研究判断）、`overall.summary`（主要结论）、`overall.trends` 与 `overall.risks`（各 3–5 条，每条首句就是观点，后文写依据）。然后：
```bash
python <SKILL_DIR>/scripts/medins.py report -w <工作区>                      # Markdown + Word
python <SKILL_DIR>/scripts/medins.py ppt -w <工作区> [--template 自有模板.pptx] [--org 部门]
```
- **报告体例**：标题 → 目录 → 一、样本与主要结论 → 二、承保范围（（一）核保端：健康告知与核保准入；（二）理赔端：既往症与赔付规则）→ 三、保障形态与版本演进 → 四、创新点识别 → 五、趋势判断与风险提示（核心发现、趋势、风险、资料缺口）→ 附录（证据清单、资料清单）。Word 为 A4、宋体正文首行缩进、表题“表N　…”在表上方、三线表、表注在表下、页脚页码；分点写法“1．**首句观点。**展开说明”。
- **PPT 体例**：16:9；封面 → 章节目录（当前章节红色高亮）→ 内容页（左上标题 + 标题下灰底结论栏 + 表格/卡片 + 底部浅蓝要点栏 + 右下页码）→ 结尾页。四章：样本概况与主要结论、核保准入与既往症、保障形态与版本演进、创新点与风险提示。`--template` 可套用用户自己的模板（按版式名匹配“标题幻灯片/目录幻灯片/仅标题/结尾幻灯片”或英文 Title Slide/Title Only）；也可在 `~/.medins-research/config.json` 写 `{"ppt_template": "<路径>", "org": "<署名>"}` 作为默认值。

产物在 `<工作区>/outputs/`：对比表 .xlsx、分析报告 .md/.docx、汇报 .pptx、校验报告.json、对比结果.json、创新点候选.json。归档原件在 `<工作区>/archive/<公司>-<系列>/产品资料/<公司>_<产品>/`（含 00_资料清单.txt 与 00_manifest.json，记录来源 URL、SHA256、页数、获取时间、证据等级、版本核对）。

向用户汇报时说明：产物路径、产品与版本范围、资料缺口（待核字段、未取得的原件）、仍有的 warning、哪些结论属于“推定”。

## 参考资料（按需读取）
- [references/workflow.md](references/workflow.md)：完整操作细节、常见问题（动态页面、证书错误、扫描件、验证码、文件被占用）
- [references/field_guide.md](references/field_guide.md)：字段口径、详细程度示例、易错点
- [references/sources.md](references/sources.md)：各类资料去哪找、检索策略、证据等级
- [references/evidence_rules.md](references/evidence_rules.md)：状态、引文核验、版本核对规则
- [references/innovation_method.md](references/innovation_method.md)：创新点判断方法与结论边界写法
- [references/agent_compat.md](references/agent_compat.md)：各 Agent 的安装位置与调用方式
