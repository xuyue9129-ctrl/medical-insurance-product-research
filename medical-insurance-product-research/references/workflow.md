# 完整操作说明

## 命令一览
| 阶段 | 命令 | 作用 |
|---|---|---|
| 环境 | `doctor` | 检查 Python、openpyxl、PDF 后端、可选库 |
| 工作区 | `init -w W --title T` | 建目录与 research.json |
| 检索 | `resolve "名称"` | 内置约 200 款医疗险目录 + 工作区目录的模糊检索 |
| | `queries "产品" --company C` | 生成检索词与资料入口提示（交给 Agent 联网搜索） |
| | `search "产品" --company C` | 内置网页检索兜底（Bing/DuckDuckGo，尽力而为） |
| | `discover URL\|html --product P --save links.json` | 列出页面中的条款/费率表等附件链接 |
| 登记 | `register --from-catalog ID` / `--company C --product P` / `--pid X --clause-name N` | 登记或更新产品；`--add-to-catalog` 写入工作区目录 |
| 归档 | `fetch --pid X URL...` / `--from-discover links.json --types 01,02` | 下载、嗅探类型、哈希、分类命名、抽文本、版本核对、证据等级 |
| | `import --pid X 文件或目录` | 复制归档本地资料（自动恢复来源索引） |
| | `gap --pid X --type 03 --status S --note N --evidence E` | 登记缺口/非缺文件说明 |
| | `verify-docs [--pid X]` | 登记备案条款名后重做版本核对 |
| 抽取 | `extract [--pid X] [--apply-safe]` | 生成字段草稿；高精度字段写入为“规则抽取” |
| 阅读 | `drafts --pid X [--field F] [--hints]` | 看候选原文句（--hints 显示样本摘要目录中的摘要，非原文） |
| | `text --pid X [--doc D] [--grep R] [--page N]` | 带页码检索/阅读原文 |
| 写入 | `record apply --pid X --file patch.json` | 批量写入并核验引文 |
| | `record set --pid X --field F --value V --doc D --quote Q [--page N]` | 单字段写入 |
| | `record show --pid X [--missing]` | 查看进度 |
| 费率 | `rates list --pid X` / `rates import --pid X --block N --columns ... --col-index ... --basis ...` | 费率表分块与搬运 |
| 校验 | `validate [--strict]` | 资料完整度、版本、引文复核、数值一致性、口径 |
| 分析 | `compare [--pair 旧:新]` / `innovate` | 横向/版本对比；创新点候选 |
| 输出 | `build`（= validate→compare→innovate→excel，`--mode series/market`）/ `deliver` / `report [--no-docx]` / `ppt [--template]` | 对比表；询问用户后按需出报告与 PPT |
| 目录 | `catalog-add --company C --product P [--user-level]` | 把新产品加入目录，下次可模糊命中 |

工作区可用环境变量 `MEDINS_WORKSPACE` 指定，省去每次 `-w`。

## 补丁 JSON 格式
数组，每项一个字段：
- 普通字段：`{"field": "<key或中文名>", "value": "…", "evidence": [{"doc": "<文件名或唯一片段>", "page": 3, "quote": "<逐字原文>"}], "note": "…", "status": "推定|待核|不适用（可省略）"}`
- 保障责任明细：`{"field": "benefits", "mode": "replace|append", "rows": [{"name", "plan", "limit", "deductible", "ratio", "scope", "evidence": [...]}]}`
- 费率表（一般用 `rates import` 自动生成）：`{"field": "rate_table", "table": {"basis", "unit", "columns", "rows": [{"age", "values"}], "evidence": [...]}}`

`doc` 可写文件名的唯一片段（如 `"01_太保互联网个人一年期住院H款"`、`"费率表"`）；`page` 可省略（脚本会定位）。

## 推荐节奏（每款产品）
1. 条款通读目录 → 2.x 保险期间/续保、等待期、免赔额、保险责任、赔付比例、责任免除、释义（既往症、医院、特需）
2. 投保须知/产品说明书 → 投保规则、健康告知说明、费率因子、服务
3. 严重既往症清单/免责附件 → 清单类别与阈值
4. 费率表 → `rates list` 选块 `rates import`；50岁保费从对应行引用
5. 清单/服务手册 → 医院家数、药品种数、CAR-T、服务项目
6. 发布稿 → 上市时间、渠道（证据 B/C）

## 常见问题
- **Windows 中文乱码**：脚本已强制 UTF-8 输出；若终端仍乱码，执行 `chcp 65001` 或设置 `PYTHONIOENCODING=utf-8`。
- **路径含空格/中文**：命令行参数加引号。
- **Excel 打开时保存失败**：输出文件被占用时会自动另存为 `_new` 文件。
- **同名产品多个版本**：用销售名区分（年份/计划/活动版），并分别登记；`compare --pair` 指定版本对。
- **字段写错**：重新 `record apply` 同字段即可覆盖；历史保留在 git 或备份中（工作区可纳入版本管理）。
- **网络受限**：先 `import` 本地资料完成研究；联网部分在可联网环境补齐后重跑 `build`。
