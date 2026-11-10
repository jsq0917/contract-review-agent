# LexFlow 对照简历审查报告

> 审查日期：2026-11-10  
> 审查对象：`E:\project\lexflow`（当前工作区 + `origin/main`）  
> 简历：`E:\ai\简历\李欣祥_简历_校招实习.docx`（**只读，未修改**；本文只给出口径建议）  
> 方法：读简历与技术档案、README、PLAN、DEMO、`lexflow/` 源码、`eval/` 脚本与产物；实际运行 `pytest`；查 SQLite 中的 API 运行记录；对 39 条 mock findings 做严格子串复核；对 3 份 API 产物做去重与双路互补统计。  
> 标注约定：**“本次实测”** 指 2026-11-10 在本机运行得到的结果；“仓库产物”指仓库里已有的脚本输出或 README 记录。所有数字都给出文件/命令出处，未使用任何估计值。

> **状态更新（2026-11-10 晚）**：本报告记录的是**修复前**的审查结论。§1–§3 的 P0/P1 项已按顺序落地：评测改为严格溯源并新增引用/未引用分列、G4 在 API 路径改记 skipped、G5 支持中文数字与换算值三态、跨路合并加入引文约束、UI 展示五闸与引文、README 数字按最新运行重写。**修复后的实测数字以 [README §4.6/§4.7](../README.md#46-mock-与真实-api-对照2026-11-10) 和 `eval/` 产物为准**；本报告中“修复前”的数字（62/86 passed、17/20 拒引、13 条劳动合同等）保留作对照，不再代表当前代码。

---

## 0. 一句话结论

工程实现比简历写得更实：离线 deterministic mock、LangGraph checkpoint、跨进程续跑、失败归档、检索评测、五道闸都是真的。但简历里有 **3 个数字/口径撑不住**（pytest 71、20 条中 17 条拒引、严格溯源 39/39 的度量方式），并且 **“五闸”和“双路”两个卖点在真实 API 路径上各有一个结构性空洞**：

- G4 在 API 引擎下退化为“有没有 cited_quote”的存在性检查，恰好漏掉它本来要挡的“条号真、法条不适用”；
- 跨路合并按 `(clause_id, risk_type)` 合并，会把同一条款下两个不同子风险拼成一条 issue，只挂一个法条，产生“issue 和 original_quote 对不上”的嵌合体，且 G4 会放行。

先改口径、同步公开仓库，再补 2–3 个可复现实验，收益远大于加新功能。

---

## 1. 最该先做的 5 件事

| 优先级 | 事项 | 改动量 | 改完后能拿出的证据 |
|---|---|---|---|
| P0 | **同步公开仓库 + 统一所有数字口径**（pytest 86/73/62/71、17→16、13→10、4→5、真实劳动合同→模板） | S（改文案/提交，0 行业务逻辑） | `pytest -q` 输出、commit hash、README 与简历同一口径 |
| P0 | **把“严格溯源”做成 eval 一等指标**，并修 `results_api.csv` 的 `note="mock 引擎"` 错误 | S（复用已有 strict 函数，约 20–40 行） | `eval` 输出 `strict_traceable=39/39`；CSV 的 note 与 engine 一致 |
| P1 | **修 G5 的两类误伤，并把 G4 在 API 下的退化说清**；把“有引用/无引用”分列统计 | M（G5 2–4h；G4 先做 10 条人工抽检；分列统计约 1h） | G5 从“12/13、7/8”变为“verified/derived/unsupported”三态；API 五闸通过率同时给 cited/uncited 两个口径；10 条引用适用性人工标注 CSV |
| P1 | **修跨路合并的嵌合体与重复条，并量化双路互补性** | M（合并键改为条款+原文；补 1 个反向测试；互补性统计约 1h） | 真实劳动合同 13 条原始 findings / 10 个去重组；`F-legal-C0005-3` 不再出现 issue 与 quote 不匹配；商务路独有数从“0（真实合同）”变为可解释数字 |
| P2 | **让 UI 展示五闸与引文，默认 mock 或明确提示成本/耗时** | M（UI 约 3–4h） | 截图里能直接看到 G1–G5、cited_quote、mock/auto 的成本提示；API 断开时不再抛 traceback |

> 如果只做 3 件：P0 的两件 + P1 的 G5/G4 口径。它们直接决定面试官第一次 clone/run/demo 时的可信度。

---

## 2. 简历原话 vs 项目实际

| 简历原话（LexFlow 条目） | 项目实际（可追溯证据） | 判定 | 建议口径 |
|---|---|---|---|
| “pytest **71 passed**（全离线、零容器）” | 本次实测 `pytest -q` = **86 passed**；忽略 3 个未跟踪测试文件 = **73 passed**；当前工作区 README:384 写 **62 passed**；`origin/main:README.md` 也是 62；全仓库 grep `71 passed` **无任何匹配** | **不成立** | 用你最终 push 的那个 commit 的实测数：提交后写 **86 passed**；若只提交现有已跟踪测试，写 **73 passed**。不要保留 71 |
| “20 条 finding 中 **17 条**模型主动拒引” | 本次实测：docx 8 条中 6 条 `law_name` 为空，pdf 12 条中 10 条为空 → **16/20**。`eval/results_api.csv` 的 G1 也是 2/8 + 2/12，反推 16 条 | **不成立（差 1）** | 改成 **16/20**；README:538 的 17 也要同步改 |
| “其中 **4 道零 LLM**” | `lexflow/verify/gates.py` 五道全是纯函数；模块 docstring 也写“G4/G5 都是纯规则，不调模型” → **5 道全零 LLM** | **不成立（且是低估）** | 改成“**五道全为纯规则**；G4 只做条款级复判，不判断法条适用性” |
| “**严格溯源 39/39**（引文为条款正文子串，非仅条号对上）” | 本次用严格子串脚本复核 39 条 mock findings，确实 **39/39**；但 `eval/run_eval.py:49-56` 与 `eval/run_synthetic_eval.py:101-106` 的 `traceable` 只检查“clause 存在 + quote 非空”，**不是子串**。只有 `eval/run_contract_smoke.py` 实现了严格子串 | **数字为真，但仓库命令量不出这句话** | 两个选择：① 把 strict 检查接进 `run_eval.py` / `run_synthetic_eval.py`，保留 39/39；② 改成“溯源 39/39（当前脚本口径：条号可定位 + 引文非空；严格子串另测 39/39）”。推荐 ① |
| “**20 份合同** / 39 条 findings（mock）” | 2 份 `examples/` 示例 + 18 份 `data/synthetic/` 合成合同；`eval/gold.jsonl` 2 条 + `data/synthetic/gold.jsonl` 18 条 | **表述不精确** | 改成“**2 份示例 + 18 份合成合同（mock）**” |
| “**真实劳动合同**：模型报 13 条、溯源 13/13” | 文件是 `examples/real/labor_contract.docx`；README:546 自己写的是“本地一份**公开劳动合同模板**”。本次严格复核 13/13 成立；但 13 条按（条款+原文）去重后是 **10 个**，且只有 **6 条**引到具体条号、**7 条**拒引 | **“真实”过强，“13 条”是原始数** | “**公开劳动合同模板（非签署合同）**：13 条原始 findings，去重后 10 个风险点；6 条引到具体条号，7 条拒引；13/13 引文可回原文” |
| “同一风险点按 **(clause_id, risk_type)** 跨路合并” | 代码确实是这个键（`lexflow/graph/state.py:39-46`）；但 `risk_type="other"` 不合并（`state.py:83-93`），同条款不同子风险会被合并成一条（见 §4.4 的 `F-legal-C0005-3`） | **不完整，且有已知副作用** | “按条款+风险类型合并；other 不合并；已知会把同条款不同子风险并成一条（待修）” |
| “**G4 语境支撑**”作为五闸之一 | `gates.py:61-66`：API 引擎没有 `rule_id` 时，G4 直接退化为“有 `cited_quote` 就通过”。本次 10 条有引用的 API finding 全部命中这条退化分支（docx 2、pdf 2、劳动合同 6） | **对 API 路径不成立** | “G4 是**条款级规则复判**（mock/规则路径）；API 路径退化为引文存在性检查，不校验法条适用性” |
| “**五闸全过 39/39**” | `eval/seeded_defect_results.md` 的对照组误报 0/39 确实成立；README:722 自己说明这是“系统自身产出的 findings，本来就该全过”的自洽检查 | **数字为真，但容易被读成准确率** | 加一句“**自洽检查，≠法律准确率**；API 路径五闸全过 3/20，主因是 16/20 未引法条” |
| “**PDF 直接取真实页码与坐标**” | `lexflow/parse/parser.py` 使用 PyMuPDF 的 page/bbox，成立 | 成立 | 保留 |
| “**docx 第几段 + 所属章节 + 第几个字到第几个字**” | `lexflow/segment/segmenter.py` 的 locator 是 `§段落 [章节] [chars start:end]`，成立；没有真实页码这一点也已在 README 说明 | 成立 | 保留 |
| “**跨进程续跑成功**” | `lexflow/service.py` 用 `Command(resume=...)` 从 checkpoint 恢复，不重跑 review；README/DEMO 有 P1/P2 两个 PID 的记录 | 成立 | 保留 |
| “补 **7 个单测**” | `tests/test_state_reducer.py` 正好 7 个 `test_*` | 成立 | 保留；但第 7 个测试只断言“两段 issue 都保留”，没有断言“合并后 citation 能同时支撑两段”（见 §4.4） |
| “**439/626（重排后）、问题命中@5 325/325**” | `eval/retrieval_results.md` 记录 439/626；325/325 是 hit@5，饱和指标 | 成立 | 保留；主指标用 **439/626**，325/325 可留但注明“饱和” |
| “真实 API 单份 **¥0.12–0.20、68–163 s**” | `eval/results_api.md`：docx ¥0.1552 / 68.5s，pdf ¥0.1217，劳动合同 ¥0.2045 / 163s | **0.2045 略高于 0.20** | 改成“**¥0.12–0.21**（最高一次 ¥0.2045）；68–163s” |
| 技能栏：“**Function Call 与 MCP 工具封装、A2A 智能体通信、ReAct 范式**” | 在 `lexflow/` 全目录 grep `tool_call|function_call|mcp|ReAct|A2A` 无任何匹配；LexFlow 是固定 DAG，没有工具调用/规划/反思循环 | **LexFlow 撑不住** | 如果 LexSynergy/LawAnswer 里也没有真实落点，删掉或降级为“了解”；否则明确写“在 X 项目中用 Function Call 做了 Y”，不要让它落在 LexFlow 上 |
| 技能栏：“**CI 回归门禁**” | 仓库没有 `.github/workflows/`，也没有 GitLab/Jenkins/Travis 配置 | **LexFlow 撑不住** | 加一个 15 行的 pytest workflow，或删掉这句话 |

---

## 3. 优化清单（按性价比排序）

### O1. 先让公开仓库和简历说同一件事（P0，改动量 S）

**问题**
- 当前工作区有 **13 个已修改文件 + 5 个未跟踪文件**，且本地分支 **behind origin/main 1 个 commit**（`git status -sb`、`git rev-list --left-right --count HEAD...origin/main` = `0 1`）。
- 公开仓库 `origin/main:README.md` 写 **62 passed**；简历写 **71 passed**；当前工作区实际 **86 passed**（忽略 3 个未跟踪测试文件是 73）。三个数字互不相同。
- `.workbuddy/` 未跟踪也未 ignore；如果之后用 `git add -A`，会把 AI 会话笔记带进公开仓库。

**为什么这对目标岗位重要**
面试官的第一步通常是 clone → 看 README → 跑 pytest。数字对不上，后面讲得再好也会先被扣一层“不严谨”。对 LLM 应用岗，可复现性和诚实性是比模型名词更稀缺的加分项。

**怎么改**
1. 先处理未提交改动：`git stash` 或先提交到本地（**不要 `git add -A`**，按文件 add）。
2. `git pull --rebase origin main`（origin 只多一个 `c5b2acf docs: README 标题改为简历项目名`）。
3. 跑 `pytest -q`，把 README:384 的 `62 passed` 改成实际值（提交全部测试后应为 **86**；只提交已跟踪测试则是 **73**）。
4. 把 `.workbuddy/` 加进 `.gitignore`；`scripts/enable-cuda.ps1` 如果只是本机实验脚本，决定是否提交。
5. push 后，用同一个 commit 的数字改简历（见 §5）。

**验收证据**
- `git status -sb` 干净、`git log -1` 是新 commit；
- `pytest -q` 输出与 README/简历的数字一致；
- GitHub 上直接看到同一个数字。

---

### O2. 把“严格溯源”变成 eval 的一等指标（P0，改动量 S）

**问题**
- `eval/run_eval.py:49-56` 的 `traceable` 定义是“clause 存在 + `original_quote` 非空”，**没有检查引文是不是条款正文的子串**。
- `eval/run_synthetic_eval.py:101-106` 的定义是“`original_quote` 非空 + 该 clause 有 `第X条` 编号”，**同样没有子串检查**。
- 简历的“严格溯源 39/39（引文为条款正文子串，非仅条号对上）”用的是 `run_contract_smoke.py` 里的严格口径，但 39 条这个集合不是由那个脚本跑出来的。
- `eval/run_eval.py:110` 的 `note` 硬编码为 `"mock 引擎，仅验证链路；比率不代表真实模型效果"`，导致 `eval/results_api.csv` 里 engine=api，note 却写 mock。这是自相矛盾，且会被面试官直接看到。

**为什么这对目标岗位重要**
“严格溯源”是你区别于“让模型自由发挥”的核心卖点。如果 eval 脚本量的是弱口径，面试官读两行代码就能问：“你这叫严格？” 反过来，把 strict 接进去后，39/39 是一个任何人能复跑的实验结论。

**怎么改**
1. 把 `run_contract_smoke.py` 里的严格子串函数抽成 `eval/metrics.py`（或直接 import），在 `run_eval.py` 和 `run_synthetic_eval.py` 输出里同时给 `traceable_weak` 与 `traceable_strict`。
2. `note` 按 `args.engine` 生成：api 就写“真实 API，样本 2，不代表整体准确率”；mock 才写 mock。
3. 重跑并更新 `eval/results_api.csv` / `eval/synthetic_results.md` / README:397-398。

**验收证据**
- 本次实测的严格复核已是 **39/39**（脚本见 §7.3）；
- 改完后 `eval/run_eval.py --engine mock` 直接打印 `strict=39/39`，不需要口头解释；
- `results_api.csv` 的 note 与 engine 一致。

---

### O3. 五闸的真实边界：G5 会误伤正确结论，G4 在 API 下空转（P1，改动量 M）

#### O3.1 G5 只认“条款原文里的阿拉伯数字”，会误伤法定值和推导值

**问题与证据**

`gates.py:77-95` 的 G5 只做一件事：把 issue 里的阿拉伯数字抽出来，逐个检查是否出现在 `finding.original_quote` 里（`gates.py:91-95`）。这会把两类**正确**结论判失败：

1. **法定值**：劳动合同 `F-legal-C0001-1`
   - issue：`试用期工资为转正后工资的70%，低于法定80%下限`
   - original_quote：`试用期工资为转正后工资的70%。`
   - 引用：`劳动合同法 第二十条`
   - G5 失败详情：`issue 中数值在条款原文找不到出处：80, 80`
   - 但第二十条原文是“不得低于……**百分之八十**”（中文数字，不是 `80`）。这是完全正确的法定比较，却被判“编造数值”。
2. **推导值**：服务合同 `F-legal-C0004-1`
   - issue：`按合同总额每日千分之五……折合年化约182.5%`
   - original_quote：`……每日千分之五……`
   - G5 失败详情：`issue 中数值在条款原文找不到出处：182.5`
   - `0.5% × 365 = 182.5%` 是正确的年化换算，不是编造。

更广义地看，G5 的 `NUM_RE = \d+(?:\.\d+)?`（`gates.py:23`）不识别中文数字/分数；语料里 `百分之`/`千分之` 共 5 条、带阿拉伯数字的法条只有 3 条（本次实测）。所以 G5 的“数值一致”只在“合同里写阿拉伯数字、issue 也复述同一个阿拉伯数字”时有效。

**为什么这对目标岗位重要**
五闸是你最技术化的卖点。一个会把正确结论判失败的闸，比没有闸更危险，因为它制造“已校验”的假象。面试官只要问“80% 为什么不在原文里”，现在只能答“闸比较保守”，很难自圆其说。

**怎么改**
1. 把 G5 从二态改成三态：
   - `verified`：数值在 `original_quote` 或 `evidence.quote` 中逐字出现；
   - `derived`：数值可由原文数值做明确换算得到（先只支持百分比年化/日利率×365、倍数×基数这类显式规则，或允许 issue 标注“计算值”）；
   - `unsupported`：既不在原文也不在法条，且无法解释——这才是需要人工看的。
2. 至少先做最小修复：把 `source` 扩展到 `original_quote + evidence.quote`，并对 `百分之X`/`千分之X` 做中文数字归一化。这样 `80` 这类法定值能过；`182.5` 仍应进入 `derived/unsupported`，而不是硬判 fail。
3. 补两个回归测试（`tests/test_gates.py` 现有 4 个 G5 测试都没覆盖这两类）：
   - 法定值：issue 含 80%，original_quote 含 70%，evidence 含“百分之八十”；
   - 推导值：issue 含 182.5%，original_quote 含“千分之五”。

**验收证据**
- 改动前：劳动合同 G5 12/13（1 条误伤）、服务合同 G5 7/8（1 条误伤）；
- 改动后：`F-legal-C0001-1` 变为 `verified`（法定值），`F-legal-C0004-1` 变为 `derived`；原始计数和分类计数都打印，不藏 fail；
- 两个新增测试在离线 pytest 中通过。

#### O3.2 G4 在 API 下不检查“法条适用性”，正好漏掉它要挡的失败模式

**问题与证据**

`gates.py` 的 docstring 写得很清楚：G4 要挡的是“引对了条文但支撑不了结论”（`gates.py:9-11`）。但 `gates.py:61-66` 在 `rule_id` 为空时直接：

```python
if finding.cited_quote:
    return True, "无规则 ID（API 引擎），G4 退化为引文存在性检查"
```

API 引擎不产出 `rule_id`，所以 **10 条有引用的 API finding 全部走这条退化分支**（docx 2、pdf 2、劳动合同 6，来自 SQLite 统计）。结果是：只要模型填了 `cited_quote`，G4 就过，完全不看这条法条是否适用于这个问题。

最具体的反例是真实劳动合同 `F-legal-C0005-3`：

- issue 第一段：`约定工伤事故由乙方自行承担全部责任……`
- issue 第二段（商务）：`……在乙方结婚或生育时解除合同的经济补偿责任……`
- original_quote：`工作中发生工伤事故，乙方需自行承担全部责任。`
- 引用：`劳动法 第九十一条`
- 第九十一条原文讲的是“克扣/拖欠工资、低于最低工资、解除后不支付经济补偿”等，**不是工伤责任**。
- G4 仍然通过，因为 `cited_quote` 非空。

这不是“法律观点不同”，而是机械的文本不匹配：issue 的一半讲结婚/生育，original_quote 讲工伤；引用的条文讲工资/经济补偿。任何面试官打开 docx 都能看到。

**为什么这对目标岗位重要**
你简历把“五道闸”作为核心，README 也承认“五道没有一道判断法条适用性”（README:718）。问题不在承认，而在简历只写“G4 语境支撑”，没有把这个边界带到简历上。面试官会追问：“G4 到底挡不挡法条错配？” 现在的真实答案是：mock 里挡条款级误报，API 里不挡。

**怎么改**
- **最小、诚实、可复现**：做一轮 **10 条引用适用性人工抽检**（从 3 份 API 产物里选 10 条有引用的 finding），逐条标 `applicable / not_applicable / unclear`，输出 CSV 和“9/10 适用”之类的原始计数。注意：这是**自审**，不是律师 gold，报告里必须标注。它直接回答“引用对不对”，成本约 2–3 小时，不需要再调 API。
- **进阶、能同时补“Function Call”卖点**：加一个 `resolve_citations` 节点，只处理无引用/低置信的 finding，工具为 `search_statutes(query, top_k)` 与 `get_article(law, article)`；模型必须返回候选条号或显式 `no_applicable_law`。这是一次真实的 tool-use/单轮 ReAct，能把“无引用 16/20”变成可测量的“引用解析率”，也能让简历的 Function Call 有落点。改动量约 1–2 天，属于加节点，不是重写架构。
- **不要**用另一个 LLM 做“是否适用”的二元判断然后把它叫“零 LLM 闸”；那会同时破坏确定性和成本口径。

**验收证据**
- 10 条人工抽检 CSV：`finding_id, cited_law, applicable, reason`，并给出 `applicable=9/10`（或真实数字）；
- 如果做 resolver：同一批 3 份合同，引用覆盖从 4/20、6/13 变为可复现的新数字，且拒引率单独报告；
- README/简历把“G4 条款级”与“法条适用性未校验”分开写。

#### O3.3 “有引用/无引用”没有真的分列统计

**问题与证据**
简历写“20 条 finding 中 17 条模型主动拒引，**故与已校验分开统计**”。但 `eval/results_api.csv` 只有 `findings_all_gates_pass` 一个总数（docx 1/8、pdf 2/12），没有 `cited`/`uncited` 两个分母。README:538 也只是解释“这是产品口径问题”，并没有真的把两组分开。也就是说，“分开统计”目前是计划，不是产物。

**怎么改**
- 在 `Finding` 或 eval 输出里加 `citation_status = cited | uncited`；
- 输出两个通过率：`gates_pass_cited`（有引用且五闸过）与 `gates_pass_all`；
- 在 README/简历里同时给两个数，例如“有引用 4 条中 3 条五闸全过；全量 20 条中 3 条”。

**验收证据**
- CSV 新增两列，数字可从同一批产物复算；
- 简历的“分开统计”有对应的列，不再是一句解释。

---

### O4. “双路审查”目前互补性很低，而且合并会产生嵌合体（P1，改动量 M）

**问题 1：两路只差一段 prompt，检索/模型/schema 完全相同**

`reviewer.py:43-53` 与 `55-65` 的两段 system prompt，除了“法务/商务”和任务聚焦那一句，后面 6 条要求（11 个 risk_type、只能引用候选法条、逐字复制、找不到就 null、必须 original_quote、空数组）**逐字相同**。`reviewer.py:167` 两路用同一个 `clause.text[:600]` 查询、同一个 retriever、同一个 top_k；`build.py:53-74` 两路共用同一个 provider/model。这不是两个能力不同的 agent，是同一个模型的两段角色提示。

**问题 2：mock 里两路完全同构**

`MockProvider` 的 `complete()` 签名里有 `system`，但实现只解析 user 消息里的 `CLAUSE_ID/TYPE/STANCE/TEXT`，然后调用 `detect_rules(clause_text, stance)`（`lexflow/llm/mock.py:19-51`），`system` 根本没被使用。所以默认 demo 的 `review_legal` 与 `review_commercial` 产出的是同一组规则命中；UI 上“审查来源=法务+商务”看起来是双路，实际第二路没有新增信息。README:103 的“LLM 调用 14 次”就是 7 条款 × 2 路，其中一半是重复计算。

**问题 3：真实 API 的互补性也很低**

本次对 3 份 API 产物按（clause_id + 归一化 original_quote）去重并统计 `reviewers`：

| 合同 | 原始 findings | 去重组 | 两路共同 | 商务路独有 | 法务路独有 |
|---|---:|---:|---:|---:|---:|
| 服务合同 docx | 8 | 7 | 6 | 1 | 0 |
| 采购合同 pdf | 12 | 11 | 4 | 1 | 5 |
| 劳动合同（唯一非示例） | 13 | 10 | 5 | **0** | 3 |
| 合计 | 33 | 28 | 15 | **2** | 8 |

也就是说：33 条里商务路只多报了 2 条独有风险；在唯一一份非示例合同上，商务路的 3 条独立 finding **全部**是与法务重复的（按 clause+quote 判定），互补为 0。这个数字本身不丢人，丢人的是简历把它讲成“双路审查”却没有量化；面试官问“第二路到底多发现了什么”，现在没有数字可答。

**问题 4：合并键会把不同子风险并成一条，且 G4 放行**

`state.py:39-46` 用 `(clause_id, risk_type)` 作为跨路合并键；同一条款下两个不同句子、不同风险，只要 risk_type 相同就会被合并。`state.py:66-74` 把两边 issue 用 `[法务]/[商务]` 拼起来，`state.py:83-93` 只给合并结果保留一份法条/引文（主条目没有时还会从其他条目补一个）。

真实劳动合同 `F-legal-C0005-3` 就是嵌合体：

- 主条目（法务）：工伤责任，quote=工伤句，引用劳动法91；
- 被并入（商务）：结婚/生育时解除的经济补偿，quote 本应是另一句；
- 合并后 issue 同时包含两段，但 original_quote/cited_quote 只有工伤/劳动法91那一套。

再叠加 O3.2 的 G4 退化，这条嵌合体五闸里 G1–G4 全过，G5 也不涉及数字，于是“13 条、溯源 13/13”把它盖住了。`tests/test_state_reducer.py:118-133` 的测试只断言“两段 issue 都保留”，没有断言“合并后 citation 能支撑两段”，所以这个行为是被测试固化的，不是偶发 bug。

**为什么这对目标岗位重要**
“双路/多智能体”是 LexFlow 最容易被追问的点。面试官不需要懂法律，也能看出“结婚生育”和“工伤”不是一回事，然后问“为什么这条引劳动法91？” 现在答不上来。

**怎么改**
1. **合并键加原文约束**：只在 `(clause_id, risk_type)` 且 `normalize(original_quote)` 相同/互相包含时合并；否则保留两条，各自带自己的 citation。这样 `F-legal-C0005-3` 会拆成工伤条（保留劳动法91，但是否适用另说）和结婚生育条（无引用），不再产生“一条 issue 两个风险、只挂一个法条”。
2. **other 的重复也处理**：当前 `risk_type="other"` 永不合并，真实劳动合同里 C0002/C0006 两对重复就是因此留下的。可以在 `other` 且 `original_quote` 与 `issue` 归一化后完全相同时合并，并保留 `reviewers`。
3. **补一个反向测试**：两条 finding 的 `original_quote` 不同但 `(clause_id, risk_type)` 相同 → 必须保持两条；以及“合并后 issue 的每一段都能在 original_quote/evidence 找到支撑”的不变量（后者可以先做成人工检查项）。
4. **量化双路互补性**：把上面的表做成 `eval` 输出（`merged_both / commercial_only / legal_only / duplicate`），每次 API 跑完自动打印。这比“双路审查”四个字有说服力得多。
5. **让 mock 两路真的不同**：如果短期不想改规则表，至少让 mock 的 legal/commercial 使用不相交的 rule 子集（法务：解除/违约金/责任/管辖；商务：付款/交付/验收/价格），这样离线 demo 也能展示“互补”，并且 mock 的 39/39 需要随之重跑。注意这是行为变更，不是调数字，重跑后的数字要如实更新。

**验收证据**
- 合并前后同一批 3 份合同：13 条劳动合同 → 仍然是 13 条或 14 条（拆开后可能 +1），但 `F-legal-C0005-3` 不再出现 issue/quote 不匹配；
- 新增测试：不同 quote 不合并、other 完全相同时才合并、合并后 citation 一致性；
- eval 输出双路互补表：商务路独有数从“真实合同 0”变成可解释数字（如果改完仍是 0，就如实写 0，并说明两路当前没有互补）。

---

### O5. 演示体验：五闸在 UI 里看不见，默认 auto 可能让面试官等 1–3 分钟（P2，改动量 M）

**问题与证据**
- `lexflow/ui/app.py` 只展示：风险等级、条款、原文、法条依据（规则 ID/法条/定位锚）、是否引用、来源、建议。**没有 G1–G5 的核验表，也没有 `cited_quote`/证据原文**（grep `G1|verification|核验|evidence|cited_quote` 在 UI 中零匹配）。五闸只存在于导出的 docx 附录里；面试官在 Streamlit 页面上看不到你最核心的差异化。
- `ui/app.py:74` 默认 `engine="auto"`。本机 `.env` 有 API key 时，auto 会走真实 API：单份 **68–163s、¥0.12–0.21**，而页面上没有成本/耗时提示（`ui/app.py:96` 只有 600s timeout）。README 的截图是显式选 mock 拍的，但面试官自己点的时候不一定会选。
- `ui/app.py:73` 的 `as_of` 默认硬编码 `2026-11-03`，今天是 2026-11-10；虽然不影响检索，但看起来像没维护。
- `ui/app.py:121` 在 mock 下显示“LLM 调用 14”，而 tokens 是 0；DEMO.md 解释过“计次不计费”，但 UI 本身没有解释，容易被问“14 次调用为什么 0 token”。
- `ui/app.py` 的 `_review`/`_health` 对 `requests` 连接异常没有 try/except；API 没起来时页面会直接抛 traceback，而不是“API 不可用”的提示。
- 首次运行 mock 仍会触发检索：`review_clauses` 先做候选召回，MockProvider 却完全不看候选；`bind_evidence` 又对每条 finding 做一次检索（`nodes.py:134-135`），即使 16/20 条没有引用。README:710 说首次建索引**可能需要约 2 分钟**（CPU 全量编码，缓存命中则秒级），所以“mock 很快”只在缓存热的时候成立。

**为什么这对目标岗位重要**
校招/实习面试的 demo 通常只有 3–5 分钟。默认 auto 让面试官等 1–3 分钟，或者首次 mock 等 2 分钟索引，都会直接吃掉演示时间；五闸看不见，等于把最强的技术点藏起来了。

**怎么改**
1. UI 加一个“核验明细”expander：每条 finding 展示 G1–G5 的 passed/detail，以及 `cited_quote` 与证据原文。数据已经在 `report["verifications"]` / `evidence` 里，不需要改图。
2. `engine` 默认改为 `mock`，或在 auto 选项旁写清“真实 API 单份约 1–3 分钟、约 ¥0.12–0.21；mock 约数秒”。让面试官自己选。
3. `as_of` 默认用 `date.today()`（或至少改成 2026-11-10），并说明“当前语料无失效日期，as_of 主要影响 G3”。
4. mock 下把指标标签改成“审查调用（mock 计次，无 token）”，或直接显示 `0 tokens / 0 元`。
5. `_review`/`_health` 捕获 `requests.RequestException`，显示可操作的错误（“API 未启动，请先运行 scripts/dev.ps1”）。
6. **mock 跳过检索**：`review_clauses` 在 `provider.engine == "mock"` 时不必召回候选；`bind_evidence` 对已知 `(law, article)` 直接查语料，不必再 search。这样 mock 不依赖 embedding 缓存，首次点击也能秒级，离线承诺更硬。

**验收证据**
- 新截图里能看到五闸表和引文；
- 默认 mock 的首次运行（清缓存后）仍在秒级；
- API 未启动时 UI 显示错误提示而不是 traceback。

---

### O6. GPU 在 service 链路 OOM：先验证“并发编码”这个假设（P2，改动量 S–M，收益不确定但很高）

**问题与证据**
- README:705 记录：直接调用 `LocalEmbedder` 的脚本路径可用 CUDA；`AnalyzeService → 图节点 → 检索` 调用 CUDA 编码时报 `cudaErrorMemoryAllocation`，降级 `bm25-only`。README 把容器内的原因归为 Docker Desktop/WDDM 的单次分配限制（768MB 失败），这个解释对容器是成立的。
- 但 **宿主机 service 链路**也 OOM，而 `build.py:90-93` 把 `review_legal` / `review_commercial` 并行 fan-out；两路都会调用 `retriever.retrieve` → `embedder.encode`。`embedder.py:66-75` 与 `hybrid.py:384` 没有任何推理锁，两个线程可以同时对同一个 SentenceTransformer 做 CUDA forward。8GB 卡上两次 forward 的峰值叠加，足以触发 OOM。
- 这不是已证实的根因，而是一个**可检验的假设**：如果 OOM 只在“双路并行 + CUDA”出现，加锁/串行化后应该消失；如果加锁后仍 OOM，再回到 WDDM/单次分配的解释。

**怎么改**
1. 在 embedder 的 `encode` 外层加一个 `threading.Lock`（或用一个小的推理队列），保证同一时刻只有一个 CUDA forward；先把两路查询串行化，再测 service 链路。
2. mock 模式跳过检索（见 O5.6），这样 mock service 完全不碰 CUDA，可以把“GPU 不可用”的影响范围缩小到 API 模式。
3. 记录测试：`LEXFLOW_EMBEDDING_DEVICE=cuda` + service 跑一份 docx，记录 `/health` 的 `embedding_device`、是否降级、耗时。成功就更新 README:705；仍失败就保留“宿主机 service CUDA 不可用”的记录，并说明已排除并发因素。

**验收证据**
- 加锁前后同一命令的日志：`vector_ready` 的 `device=cuda` vs `bm25-only`；
- 如果成功，README 的“交付口径按 CPU 运行 service”可以改成分情况说明（脚本路径/ service 路径），这是一个实打实的工程结论。

---

### O7. 小而值的修补（P2，改动量 S）

| 事项 | 证据 | 改法 | 价值 |
|---|---|---|---|
| `results_api.csv` 的 note 写成 mock | `eval/run_eval.py:110` 硬编码；CSV 里 engine=api、note=mock | 按 engine 生成 note | 避免“产物自相矛盾” |
| CI 缺失 | 无 `.github/workflows/`；简历写“CI 回归门禁” | 15 行 workflow：uv sync + pytest -q | 让简历这句话有落点 |
| `.workbuddy/` 未 ignore | `git status` 显示 `?? .workbuddy/`；`.gitignore` 无匹配 | 加 `.workbuddy/` | 防止误提交 AI 笔记 |
| `as_of` 硬编码 | `ui/app.py:73` = 2026-11-03 | 改 `date.today()` 或 2026-11-10 | 演示不显得过期 |
| mock 的“LLM 调用 14” | `nodes.py:112` 计的是 reviewer 调用；`ui/app.py:121` 标签是 LLM 调用 | 改标签或 mock 下显示 0 | 避免“0 token 怎么 14 次调用” |
| UI 无连接异常处理 | `ui/app.py` 的 `requests.post` 无 try/except | 捕获 `RequestException` | API 没启动时不抛 traceback |
| API 同步阻塞 | `api/main.py` 的 `async def analyze` 直接调用阻塞的 `service.analyze`，68–163s 期间事件循环被占，`/health` 无法响应 | 改成同步 `def analyze`（FastAPI 走线程池）或 `run_in_threadpool` | 单机演示也能并发；但要注意 O6 的编码锁 |

> API 异步任务/队列：**现在不建议做**。README 已说明这是单机演示；简历也没写“高并发/生产级”。把时间花在 O1–O4 更划算。

---

### O8. 明确不建议做的事

- **不要为了把五闸通过率做好看去放宽 G4/G5。** 先把“有引用/无引用”分列，再修真实的误伤；否则就是凑数。
- **不要给合成数据加更多规则来提高 precision。** README:724 已说明合成 precision 是链路一致性，不是法律准确率；继续加只会让简历更虚。
- **不要重写 LangGraph 架构。** 问题是局部的：merge key、G5 的数值归一化、G4 的适用范围、UI 展示。架构本身（checkpoint、interrupt、并行、reducer）是成立的。
- **不要加 Postgres/Milvus/Redis/MinIO。** M0 的 SQLite + FAISS + 文件存储够用；加中间件不会提高面试说服力，反而增加跑不起来的风险。
- **不要改 G2 的“全文逐字相等”。** 当前语料最大条款 386 字（1465 条，>500 字为 0），模型在 10 条有引用的 API finding 上 G2 **10/10** 通过；这不是当前瓶颈。真要做，也只是把“必须等于全文”改成“必须是原文的连续子串且长度≥N”，优先级低于 G4/G5。
- **不要把 325/325 当主指标。** 它是 hit@5 饱和值；主指标用 439/626。简历里保留可以，但不要单独拿出来讲。
- **不要为了“多智能体”再加一个只换 prompt 的第三个 agent。** 先量化互补性；如果两路本来就 90% 重复，第三个只会更贵。

---

## 4. 关键证据的细节（便于复查）

### 4.1 测试数字

| 口径 | 数字 | 出处 |
|---|---:|---|
| 当前工作区全量 | **86 passed** | 本次实测 `pytest -q`（2026-11-10） |
| 忽略 3 个未跟踪测试文件 | **73 passed** | 本次实测 `pytest -q --ignore=tests/test_retrieve_rerank.py --ignore=tests/test_retriever_recovery.py --ignore=tests/test_ui.py` |
| 当前工作区 README | 62 passed | `README.md:384`（2026-11-07 复核） |
| `origin/main` README | 62 passed | `git show origin/main:README.md` |
| 简历 | 71 passed | 简历 LexFlow 条目 |
| 仓库中 `71 passed` 的痕迹 | **无** | `grep -R "71 passed"` 零匹配 |

### 4.2 API 产物的 findings / 引用 / 闸门

数据来自本机 `data/lexflow.db`（本次实测 SQL 查询）：

| matter | 合同 | findings | 无 `law_name` | G4 退化通过 | G5 fail |
|---|---|---:|---:|---:|---:|
| `matter-140be86bf3` | 服务合同 docx | 8 | 6 | 2 | 1（182.5） |
| `matter-65929542a6` | 采购合同 pdf | 12 | 10 | 2 | 0 |
| `matter-26a487bbf3` | 劳动合同 | 13 | 7 | 6 | 1（80, 80） |
| **合计** | 3 份 | **33** | **23** | **10** | **2** |

> 结论：API 的 4 条有引用 finding 全在 docx/pdf 里（2+2），劳动合同 6 条；10 条有引用的 finding **全部**走了 G4 退化分支，G4 没有做过一次真正的适用性判断。20 条示例 finding（docx+pdf）中 **16 条**无引用，不是 17。

### 4.3 严格溯源复核

本次对 `eval/gold.jsonl`（2 条）+ `data/synthetic/gold.jsonl`（18 条）逐份跑 mock，收集 39 条 finding，按 `normalize(original_quote) in normalize(clause.text)` 复核：

```
STRICT_TRACE 39 / 39
```

脚本见 §7.3。这个数字支持简历的 39/39，但**仓库里的 `run_eval.py` / `run_synthetic_eval.py` 不做这个检查**，所以需要 O2 把它接进去。

### 4.4 真实劳动合同的重复与嵌合体

本次按（clause_id + 归一化 original_quote）分组：

```
LABOR findings 13 unique_clause_quote 10 merged_both 5 single 8 single_unique 3
```

- 13 条原始 findings → **10 个（条款+原文）去重组**，即 3 条重复；
- 商务路独有的 3 条 finding **全部**是法务条目的重复（C0002、C0005 工伤、C0006），所以商务路在这份合同上的独有贡献是 **0**；
- `F-legal-C0005-3` 的 issue 同时包含“工伤”和“结婚/生育”，original_quote 只有“工伤”，引用是劳动法91；这是合并键 + G4 退化叠加的结果。

### 4.5 G5 两个误伤的具体文本

**法定值（劳动合同）**

```
finding: F-legal-C0001-1
issue:   [法务] 试用期工资为转正后工资的70%，低于法定80%下限……；[商务] ……低于法定不得低于劳动合同约定工资80%的下限……
quote:   试用期工资为转正后工资的70%。
law:     中华人民共和国劳动合同法 第二十条
G5:      fail, "issue 中数值在条款原文找不到出处：80, 80"
第二十条原文: ……不得低于……百分之八十……
```

**推导值（服务合同）**

```
finding: F-legal-C0004-1
issue:   [法务] ……按合同总额每日千分之五……；[商务] ……折合年化约182.5%……
quote:   任何一方违约的，应按合同总额每日千分之五向对方支付违约金，违约金不设上限。
G5:      fail, "issue 中数值在条款原文找不到出处：182.5"
换算:    0.5% × 365 = 182.5%
```

### 4.6 语料数字形态

本次实测：1465 条法条，**最大条款 386 字**，`>500` 字为 **0**；含 `百分之`/`千分之` 的 **5 条**，含 `%` 的 **0 条**，含阿拉伯数字的 **3 条**。这解释了为什么 G5 在 mock 的阿拉伯数字模板上工作，在真实法条的中文数字上会误伤。

---

## 5. 简历改口建议（只给口径，不改文件）

> 简历文件保持只读；下面是可以直接抄进简历的替换句。原则：**数字来自你最终 push 的 commit；口径写成项目真正能复现的东西；不把“自洽检查”写成“准确率”。**

### 5.1 必须改的 4 处

| 现在 | 建议改成 |
|---|---|
| `pytest 71 passed` | `pytest 86 passed（当前工作区全量；公开仓库同步后同口径）`。如果只提交现有已跟踪测试，则写 `pytest 73 passed`。**不要保留 71** |
| `20 条 finding 中 17 条模型主动拒引` | `20 条 finding 中 16 条（docx 6/8、pdf 10/12）模型主动拒引；有引用 4 条、其中 3 条五闸全过` |
| `其中 4 道零 LLM` | `五道均为纯规则；G4 只做条款级复判，不判断法条适用性` |
| `严格溯源 39/39（引文为条款正文子串，非仅条号对上）` | `严格子串复核 39/39（由 eval 的 strict 模式输出）；该指标只证明引文可回原文，不证明法条适用` |

### 5.2 需要补限定的 5 处

| 现在 | 建议改成 |
|---|---|
| `20 份合同 / 39 条 findings（mock）` | `2 份示例 + 18 份合成合同（mock） / 39 条 findings` |
| `真实劳动合同` | `公开劳动合同模板（非签署合同）` |
| `模型报 13 条、溯源 13/13` | `13 条原始 findings（按条款+原文去重后 10 个风险点）；6 条引到具体条号、7 条拒引；13/13 引文可回原文` |
| `五闸全过 39/39` | `自洽检查 39/39（≠法律准确率）；API 路径五闸全过 3/20，主因 16/20 未引法条` |
| `同一风险点按 (clause_id, risk_type) 跨路合并` | `按条款+风险类型合并；other 不合并；已知会把同条款不同子风险并成一条（待修）` |

### 5.3 建议删掉/转移的 3 处

| 现在 | 建议 |
|---|---|
| 技能栏 `Function Call 与 MCP 工具封装、A2A 智能体通信、ReAct 范式` | 如果 LexSynergy/LawAnswer 里也没有真实落点，删掉或降级为“了解”。**LexFlow 里没有 tool call/MCP/A2A/ReAct**，不要让它落在这个项目上 |
| 技能栏 `CI 回归门禁` | 给 LexFlow 加一个 pytest workflow（15 行），或删掉 |
| `单份 ¥0.12–0.20` | 改成 `¥0.12–0.21`（最高一次 ¥0.2045），或写 `约 ¥0.12–0.20`，不要写成严格上界 |

### 5.4 如果做完了 O3/O4，可以加的两句（有证据再加）

- `引用适用性：10 条人工抽检中 9 条适用（自审，非律师 gold）；G4 在 API 路径的退化已单列。`
- `双路互补：3 份合同 33 条 findings 中，商务路独有 2 条、法务路独有 8 条；唯一非示例合同上商务路独有为 0。`

---

## 6. 面试追问预演

| 面试官可能问 | 现在的真实回答（弱点） | 做完 O1–O4 后的回答 |
|---|---|---|
| “你的双路/多智能体，第二路到底多发现了什么？” | “商务视角” —— 但数据是 33 条里商务独有 2 条，唯一真实合同 0 条；mock 两路完全相同 | “我量化了互补性：33 条里商务独有 2 条；真实合同上为 0，所以我后来把两路的检索范围和输出 schema 分开了/加了裁决节点，互补性变成 X” |
| “五闸全过 39/39，是不是说明结论都对？” | 不是，是自洽检查；API 路径 G4 退化、G5 误伤；C0005-3 的引用和 issue 不匹配 | “39/39 只证明判据写对；API 路径我把 cited/uncited 分开，有引用的 4 条里 3 条五闸全过；另外做了 10 条引用适用性人工抽检，9/10 适用” |
| “严格溯源怎么算的？” | `run_eval.py` 里其实是“条号可定位 + 引文非空”，不是子串 | “strict 模式按归一化子串算，39/39；弱口径和严格口径都输出，避免混用” |
| “真实合同效果如何？” | 1 份模板，13 原始/10 去重，6 条引用，无律师 gold | “1 份公开模板，13 原始/10 去重；6 条引用、7 条拒引；我做了自审和引用抽检，不冒充 gold” |
| “pytest 多少？” | 简历 71，README 62，实际 86/73 | “当前 commit 86 passed，全离线；CI 也跑同一套” |
| “GPU 为什么在 service 里 OOM？” | 容器 WDDM/单次分配；宿主机 service 也 OOM，未解释 | “我先验证了并发编码假设：两路并行对同一模型 forward，加锁后重测；结果 X” |
| “Function Call/MCP/A2A/ReAct 用在哪？” | LexFlow 里没有 | “LexFlow 里没有；我在 X 项目里用 Function Call 做了 Y” 或 “目前是了解，没在生产项目落地” |
| “为什么 /analyze 同步阻塞 68–163s？” | 单机演示，没做异步任务 | “当前是单机演示；要上线会改成后台任务 + 轮询/SSE，前端先拿 task_id” |

---

## 7. 复现附录

### 7.1 测试

```powershell
cd E:\project\lexflow
.\.venv\Scripts\python.exe -m pytest -q
# 本次: 86 passed

.\.venv\Scripts\python.exe -m pytest -q `
  --ignore=tests/test_retrieve_rerank.py `
  --ignore=tests/test_retriever_recovery.py `
  --ignore=tests/test_ui.py
# 本次: 73 passed
```

### 7.2 API 产物统计（SQLite）

```python
import sqlite3
con = sqlite3.connect("data/lexflow.db"); con.row_factory = sqlite3.Row
for mid in ("matter-140be86bf3", "matter-65929542a6", "matter-26a487bbf3"):
    n = con.execute("select count(*) from findings where matter_id=?", (mid,)).fetchone()[0]
    unc = con.execute("select count(*) from findings where matter_id=? and (law_name is null or law_name='')", (mid,)).fetchone()[0]
    deg = con.execute("select count(*) from verifications where matter_id=? and gate='G4' and passed=1 and detail like '%退化为引文存在性%'", (mid,)).fetchone()[0]
    g5 = con.execute("select count(*) from verifications where matter_id=? and gate='G5' and passed=0", (mid,)).fetchone()[0]
    print(mid, n, unc, deg, g5)
# 8 6 2 1 / 12 10 2 0 / 13 7 6 1
```

### 7.3 39 条严格溯源复核（本次实测脚本）

```python
import json, re, sys
from pathlib import Path
sys.path.insert(0, ".")
from lexflow.service import AnalyzeService
from lexflow.parse import parse_document
from lexflow.segment import segment_clauses

PUNCT = " \t\n\r，。；：、,.!?;:\"'“”‘’（）()《》【】[]…—-"
norm = lambda s: "".join(ch for ch in (s or "") if ch not in PUNCT)

svc = AnalyzeService()
total = strict = 0
for gp in (Path("eval/gold.jsonl"), Path("data/synthetic/gold.jsonl")):
    for line in gp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        p = Path(row.get("sample") or (gp.parent / row["file"]))
        if not p.exists():
            continue
        res = svc.analyze(p, stance=row.get("stance", "甲方"), as_of=row.get("as_of"),
                          engine="mock", require_confirmation=False, auto_approve=True)
        clauses = {c.clause_id: c.text for c in segment_clauses(parse_document(p))}
        for f in res.report.get("findings", []):
            total += 1
            if f.get("original_quote") and norm(f["original_quote"]) in norm(clauses.get(f["clause_id"], "")):
                strict += 1
print("STRICT_TRACE", strict, "/", total)
# 本次: STRICT_TRACE 39 / 39
```

### 7.4 双路互补性统计

```python
import sqlite3
con = sqlite3.connect("data/lexflow.db"); con.row_factory = sqlite3.Row
PUNCT = " \t\n\r，。；：、,.!?;:\"'“”‘’（）()《》【】[]…—-"
norm = lambda s: "".join(ch for ch in (s or "") if ch not in PUNCT)

for mid in ("matter-140be86bf3", "matter-65929542a6", "matter-26a487bbf3"):
    fs = con.execute("select finding_id,clause_id,reviewers,original_quote from findings where matter_id=?", (mid,)).fetchall()
    groups = {}
    for f in fs:
        groups.setdefault((f["clause_id"], norm(f["original_quote"])), []).append(f)
    both = sum(1 for f in fs if "legal" in (f["reviewers"] or "") and "commercial" in (f["reviewers"] or ""))
    single = [f for f in fs if not ("legal" in (f["reviewers"] or "") and "commercial" in (f["reviewers"] or ""))]
    uniq = [f for f in single if len(groups[(f["clause_id"], norm(f["original_quote"]))]) == 1]
    cu = [f for f in uniq if "commercial" in (f["reviewers"] or "")]
    lu = [f for f in uniq if "legal" in (f["reviewers"] or "")]
    print(mid, "total", len(fs), "unique", len(groups), "merged", both,
          "single_uniq", len(uniq), "commercial_uniq", len(cu), "legal_uniq", len(lu))
# 本次:
# 140be86bf3 total 8 unique 7 merged 6 single_uniq 1 commercial_uniq 1 legal_uniq 0
# 65929542a6 total 12 unique 11 merged 4 single_uniq 6 commercial_uniq 1 legal_uniq 5
# 26a487bbf3 total 13 unique 10 merged 5 single_uniq 3 commercial_uniq 0 legal_uniq 3
```

### 7.5 仓库状态

```text
git status -sb      ->  M .gitignore, README.md, lexflow/api/main.py, ... (13 modified)
                       ?? .workbuddy/, scripts/enable-cuda.ps1, tests/test_retrieve_rerank.py, tests/test_retriever_recovery.py, tests/test_ui.py
git rev-list --left-right --count HEAD...origin/main -> 0  1
origin/main         -> c5b2acf docs: README 标题改为简历项目名
                       cc28b14 新增 Streamlit 界面实拍截图（engine=mock）与 MIT 许可证
```

> 结论：本地并不领先远端，工作区还有较多未提交改动；在改简历数字前，先把“要展示的那个 commit”确定下来。

---

## 8. 最后：如果只有一天

按这个顺序做，收益最大：

1. **上午**：提交/同步公开仓库，跑 `pytest`，把 README 和简历的 86/73、16/20、4→5 改对（O1）。
2. **下午**：把 strict 接入 `run_eval.py` / `run_synthetic_eval.py`，修 CSV note，重跑产物（O2）。
3. **晚上**：给 G5 加“原文 + 证据 + 中文数字”的最小修复，补两个回归测试；把 `F-legal-C0005-3` 的嵌合体写进“已知问题/待修”，不要让它留在简历的“每条结论可点回原文”后面（O3/O4）。

如果还有第二天：再做 UI 的五闸/证据展示和默认 mock（O5），以及 embedder 加锁验证 CUDA（O6）。这两项是“锦上添花”，但前面三项是“别被问倒”。

---

*本报告只读审查，未修改 `E:\ai\简历` 下任何文件，也未改动 lexflow 业务代码。*
