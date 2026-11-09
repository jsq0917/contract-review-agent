# lexflow 后续阶段规划（M0 之后）

> 本文件是阶段执行依据。所有验收只认本地、离线、无 API key 条件下能机械复跑的结果；不设效果目标，不把合成数据结果写成真实能力。

## 0. 当前盘点结论（扫描 `E:\project` 全目录）

扫描范围：`E:\project` 下全部项目（排除 `.venv`、`node_modules`、`site-packages` 等依赖目录）。
结论：**法律法条原文充足；真实合同、律师标注、裁判文书、合同审查 gold 均无。**

### 0.1 可用（可直接使用）

| 资产 | 实际路径 | 规模/说明 |
|---|---|---|
| 民法典全文 Markdown | `E:\project\LawAnswer-doubao\data\raw\中华人民共和国民法典.md`（`LawAnswer`、`LawAnswer-api` 下有同名副本） | 345.3 KB，全法典 1260 条；lexflow 已从中抽取合同编 463–988 共 526 条 |
| 劳动法 Markdown | `E:\project\LawAnswer-doubao\data\raw\中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md` | 28.5 KB，107 条 |
| 劳动合同法 Markdown | `E:\project\LawAnswer-doubao\data\raw\中华人民共和国劳动合同法.md` | 33.7 KB，98 条 |
| 三部法律 PDF | `E:\project\LawAnswer-doubao\data\src\` 下三个 PDF | 可用于 PDF 解析/页码/bbox 回归测试；版式复杂，文本抽取以 MD 为准 |
| 结构化法条（1465 条） | `E:\project\LexSynergy Legal QA System\data\corpus\articles.jsonl` | 民法典 1260 + 劳动法 107 + 劳动合同法 98；字段 `law_name/doc_id/article_no/article_num/chapter_path/content` |
| 法条分块（2935 块） | `E:\project\LexSynergy Legal QA System\data\corpus\chunks.jsonl` | 检索评测可复用 |
| 法条语料（带 sha256） | `E:\project\LexWeave\data\corpus\articles.jsonl`、`manifest.json` | 1465 条，`by_law: cc 1260 / ll 107 / lcl 98` |
| 本地 embedding 模型 | `E:\project\模型\bge-m3` | 2.16 GB（`pytorch_model.bin`），可离线加载；需 `sentence-transformers` |
| 本地 reranker 模型 | `E:\project\模型\bge-reranker-v2-m3` | 2.16 GB（`model.safetensors`） |
| 中文文档分段模型 | `E:\project\模型\nlp_bert_document-segmentation_chinese-base` | 388 MB，未接入 |
| 法条引用/闸门实现参考 | `E:\project\LexWeave\common\law_utils.py`、`E:\project\LexWeave\app\__007__agents\__001__gates.py`、`E:\project\LexSynergy Legal QA System\app\verifier\gates.py` | 条号归一化、G1–G5 纯函数思路，可移植/改写 |
| 证据账本/入库清洗参考 | `E:\project\LexSynergy Legal QA System\app\evidence\ledger.py`、`app\ingest\normalize.py` | 证据记录结构与清洗规则 |
| 文档加载参考 | `E:\project\LawAnswer-doubao\app\ingest\loaders.py`、`chunker.py` | Markdown/PDF 读取与分块；不含 docx 表格 |
| HITL/checkpoint 参考 | `E:\project\LexWeave\app\__006__hitl\__001__checkpoint.py` | 人工确认与断点恢复思路 |
| 法律问答评测集 | `E:\project\LawAnswer\eval\qa_test_cases.jsonl`；`E:\project\LexSynergy Legal QA System\eval\qa_set_100.jsonl`、`qa_set_130.jsonl`、`calc_cases.jsonl`；`E:\project\LexWeave\data\gold\gold_200.jsonl` | 可用于**法条检索/问答**评测，**不能**当合同条款审查 gold |
| lexflow 现有资产 | `e:\project\lexflow\data\corpus\law_articles.jsonl`（526 条）、`examples/`（2 份自造合同）、`eval/` | M0 已跑通；样本量不足以统计 |

### 0.2 不可用（扫描后确认无）

| 需要的数据/资源 | 结论 |
|---|---|
| 真实企业/律所合同样本 | **无**。全盘 28 个 docx、23 个 pdf 中，法律类只有三部法律 PDF 与 lexflow 自造样例；其余为教育/电商文档 |
| 律师标注的合同审查 gold | **无** |
| 裁判文书/类案库 | **无**。`E:\project\LexWeave\data\cases\case_bank.jsonl` 共 116 行，是项目缺陷回归记录，不是裁判案例 |
| 扫描件合同样本 | **无**。`E:\project\Itcast_qa_system\rag_qa\data\samples\ocr_02.docx`、`ocr_03.pdf` 是课程 OCR 样例，非合同 |
| 真实合同分布/线上日志/业务量 | **无** |
| 已验证可用的模型 API | **无验证**。当前环境变量 `DEEPSEEK_API_KEY` 存在，但未读取其值、未联网验证；所有阶段验收不使用它 |
| GPU | 实测存在 `NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB`（与“无 GPU”预设不符，如实记录）；但验收不依赖 GPU |

### 0.3 需转换（有原始材料，但格式/口径不匹配）

| 原始资产 | 转换目标 | 注意点 |
|---|---|---|
| 民法典 Markdown 全文 | lexflow `LawArticle` JSONL（1260 条） | 现有 `scripts/build_corpus.py` 只取合同编 526 条；扩全量需保留条号、章节 |
| 劳动法/劳动合同法 Markdown | 同上（107 + 98 条） | 劳动法文本含 1122 个康熙部首字符，需先归一化 |
| LexSynergy `articles.jsonl` | lexflow JSONL | 缺 `effective_from/effective_to/source_url/version_id`，需补元数据或改由 MD 重建 |
| LexWeave `gold_200/gold_60` | 法条检索评测子集 | 是问答/图检索 gold，不是合同审查 gold，不可直接复用 |
| 本地 bge-m3 | lexflow `LocalEmbedder` | 需安装 `sentence-transformers`，并把模型路径指向本地目录；不可联网下载 |
| Itcast OCR 样例 | OCR 依赖错误路径测试 | 只能验证“缺依赖/扫描件报错”，不能当合同样本 |
| lexflow 2 份自造合同 | 阶段 1 的合成合同数据集 | 需加入注入规则、逐份 gold、可复现种子与声明 |

## 1. 项目目标（应用岗口径）

- 目标：交付一个**可运行、可验证、成本可解释**的合同审查多智能体原型，而不是刷一个无法复现的准确率。
- 口径：离线可跑；无 key 可演示；每个数字都能追溯到生成脚本/运行日志；明确区分 mock、合成数据与真实效果。
- 本轮不做：类案检索、知识图谱、时间线、完整金额测算（M0 边界，后续也不在本计划内）。

## 2. 数据替代策略

1. **合同数据**：使用可复现的合成合同生成器（固定种子 + 模板 + 风险条款注入）。生成结果标注“合成数据，非真实合同分布”。
2. **Gold**：由注入规则反推——哪一条被注入了哪类风险，就是该条的 gold。这样能机械计算条款级 recall/precision，但只衡量“注入风险是否被检出”，**不代表真实合同上的法律质量**。
3. **法条数据**：使用 `E:\project` 内真实法条原文（民法典/劳动法/劳动合同法），不用合成法条。
4. **模型**：默认 mock（零 token、确定性）；真实 API 仅作为可选路径，不参与验收。
5. **向量**：本地 bge-m3 可选接入；依赖或模型不可用时保持 BM25-only，不崩。
6. **OCR**：无扫描件合同样本，不纳入评测；仅保留“缺依赖时明确报错”的路径。

## 3. 技术方案（延续 M0，不换栈）

- 编排：LangGraph 1.x 单主图 + `interrupt()` 单人确认 + SQLite checkpointer。
- 解析：PyMuPDF（page/bbox/char_offset）、pdfplumber（表格）、python-docx。
- 检索：法条 JSONL + BM25（中文 2-gram）+ 可选本地 bge-m3 向量 + RRF。
- 审查：provider 抽象（mock/OpenAI 兼容/auto），mock 为确定性规则。
- 核验：G1 条号存在、G2 逐字引文、G3 as_of 时效，纯函数 + pytest。
- 存储：SQLite 单库（业务表 + checkpoint 表）。
- 交付：FastAPI + Streamlit + docxtpl + CLI。
- 评测：`eval/` 输出 CSV/Markdown，仅原始计数与比率；无 gold 时输出“无标注数据，指标不可计算”。

## 4. 依赖项与降级

| 依赖 | 现状 | 缺失时的降级 |
|---|---|---|
| 真实合同 + 律师 gold | 无 | 合成合同 + 注入式 gold（仅链路指标） |
| 模型 API key | 未验证 | `engine=mock`，token=0、成本=0 |
| `sentence-transformers` / `faiss-cpu` | 未安装（可选 extra） | BM25-only，`/health` 显示模式 |
| 本地 bge-m3 | 存在于 `E:\project\模型` | 不加载，继续 BM25-only |
| OCR 依赖 + 扫描件样本 | 依赖可选、样本无 | 遇到扫描件抛 `OCRRequiredError`；缺依赖抛 `OCRDependencyError` |
| 网络 | 验收不需要 | 全流程离线 |
| GPU | 有（RTX 4060 8GB） | 不依赖；CPU 路径可跑 |

## 5. 阶段划分（按数据依赖排序）

> 每阶段结束停下汇报，等确认后再进入下一阶段。

### 阶段 1：合成数据与注入式 gold（本阶段）

- 交付物：
  - `lexflow/synthetic.py`：可复现生成器（固定种子、模板、风险注入、文本哈希）
  - `scripts/gen_synthetic_contracts.py`：生成 12 份合成 docx + `data/synthetic/gold.jsonl` + `manifest.json`
  - `eval/run_synthetic_eval.py`：条款级 recall/precision、溯源率、分闸门计数、耗时、docx 行数/DB 条数
  - `tests/test_synthetic.py`：复现性、gold 与合同一一对应、注入条号存在、端到端
- 机械验收：
  1. `uv run pytest -q` 全绿；
  2. 同一种子重复生成，文本哈希完全一致；
  3. 12 份合同 ↔ 12 条 gold 一一对应，gold 中的风险条号在合同原文存在；
  4. `eval` 输出 CSV/Markdown，含真实计数与比率，无目标值；
  5. 至少 1 份合成合同经 mock 端到端导出 docx，docx 风险表数据行数 = DB findings 数。
- 风险：模板简单导致“检测规则与注入规则同构”，precision 可能天然接近 1；只能说明链路闭环，不能说明真实审查能力。声明必须写进产物与报告。

### 阶段 2：真实法条语料统一与本地向量

- 交付物：全量 1260 条民法典 + 劳动法/劳动合同法结构化 JSONL；本地 bge-m3 接入；语料来源清单。
- 验收：转换脚本幂等；`pytest` 断言条数/条号唯一/关键条存在；无模型时仍 `bm25-only`；检索输出原始计数（top-k 命中条号表），不设目标。
- 风险：康熙部首/页脚污染导致条号重复；向量依赖安装可能失败 → 保持 BM25 降级。

### 阶段 3：审查能力与结构化抽取

- 交付物：mock 规则按风险类型扩展（覆盖更多条款类型）；LLM JSON schema 校验与失败重试；失败案例归档。
- 验收：规则单测；合成集按风险类型输出计数；无 API 时全绿；非法 JSON 被拒绝而不是静默接受。
- 风险：规则越复杂越像“为数据写死”；需把规则与评测分开，避免自证。

### 阶段 4：交付与 HITL 打磨

- 交付物：docx 报告结构完善（风险表/核验表/免责声明）；API/UI 的确认与下载；checkpoint 恢复；日志字段完整性测试。
- 验收：interrupt→resume e2e；docx 行数=DB；日志 JSON 必含 `matter_id/node/model/tokens/latency/status`；非法 matter 返回 404/409。
- 风险：UI/接口只做单机演示，不能宣称并发/多租户能力。

### 阶段 5：离线演示包与文档

- 交付物：README 与 `docs/` 统一口径；一键脚本；复跑命令清单；评测报告模板（只填真实计数）。
- 验收：按 README 从零执行命令可复现；所有数字标注来源与性质；无“效果良好”类结论。
- 风险：文档与实际行为漂移 → 以脚本输出为准，README 只引用实测。

## 6. 本计划明确不承诺的事

- 不承诺任何准确率/召回率/通过率/时延目标；
- 不把合成数据、mock 输出表述为真实合同审查效果；
- 不承诺真实 API 下的成本与性能；
- 不承诺类案、图谱、时间线、金额测算能力。

## 7. 阶段 1 执行记录（已完成）

交付：

- `lexflow/synthetic.py`：3 模板 × 4 变体，固定种子 `20261103`，注入 4 类风险，共 12 份合同、18 处注入。
- `scripts/gen_synthetic_contracts.py`：生成 docx + `gold.jsonl` + `manifest.json`。
- `eval/run_synthetic_eval.py`：条款级 recall/precision、溯源率、分闸门计数、耗时、docx/DB 一致性。
- `tests/test_synthetic.py`：5 个机械验收用例。

实测（本机单次运行）：

- `uv run pytest -q`：`15 passed in 3.93s`（其中 5 个为阶段 1 新增）。
- 生成：12 份合同、18 处注入；gold 与合同一一对应。
- 合成集评测：条款召回 18/18；条款 precision 18/22 = 0.8182；溯源 44/44；三闸全过 38/44；调用 192 次；tokens 0/0。
- 误报来源（实测）：设备采购模板交付条款中的“预付款”被子串匹配为“付款”，产生 2 个非注入条款误报；该问题留到阶段 3 处理。

结论边界：以上只验证“注入风险能否被 mock 规则检出”和链路一致性，不是真实合同审查效果。

## 8. 阶段 2 执行记录

### 8.1 统一法条语料（已完成）

交付：

- `lexflow/corpus_build.py`：从 `E:\project` 内 4 个既有文件构建 1465 条统一语料；幂等、无时间戳。
- `scripts/build_corpus.py`：薄包装，默认输出 `data/corpus/`。
- `data/corpus/law_articles.jsonl`：1465 条，字段
  `law_id/law_name/article_no/chapter_path/text/effective_from/effective_to/source_url/version_id/content_hash`。
- `data/corpus/labor_law_normalization.jsonl`：劳动法 107 条归一化前后记录。
- `data/corpus/corpus_manifest.json`：来源路径、条数、缺失元数据原因、文件 SHA256。
- `lexflow/retrieval/corpus.py`：`LawArticle` 增加 `law_id/chapter_path/content_hash`，支持空生效日期。

机械验收（实测）：

- 条数：总计 1465；民法典 1260、劳动法 107、劳动合同法 98；`(law_id, article_no)` 唯一。
- 幂等：连续两次构建，`law_articles.jsonl` SHA256 均为
  `cc76c77435b06a1059070ccc7c4516e3cbef9261fb9a139cd365df61638db642`。
- 康熙部首：源文件 1122 字，正文替换 1018 字，影响 107 条；归一化后劳动法正文部首残留 0。
- `effective_from` 全部非空（仅填写源文件明示的施行日期）；`effective_to` 全部留空；
  劳动合同法 `source_url` 留空。缺失原因写入 manifest 与 README。
- `pytest` 新增断言：条数、唯一性、`content_hash`、归一化日志完整性、构建幂等、向量缺失降级。

### 8.2 本地 bge-m3 与降级（已完成）

交付：

- `lexflow/retrieval/embedder.py`：`sentence-transformers` 加载本地 `E:\project\模型\bge-m3`；
  `device=auto` 优先 CUDA，否则 CPU；失败时写 `reason`，不抛异常。
- `lexflow/retrieval/reranker.py`：本地 `E:\project\模型\bge-reranker-v2-m3`，默认关闭，评测显式启用。
- `lexflow/retrieval/hybrid.py`：`bm25` / `dense` / `hybrid` 三模式；RRF 融合；向量磁盘缓存；
  reranker 不可用或失败时返回 hybrid 结果并记录原因。
- `lexflow/config.py`：本地模型路径、设备、缓存目录、reranker 开关。
- `lexflow/service.py`：把上述配置传入检索器；`api/main.py` 的 `/health` 增加
  `vector_backend/vector_reason/embedding_device/reranker_enabled`。

实测环境：

- `torch 2.15.0+cpu`，`torch.cuda.is_available()=False`，`device_count=0`。
- 硬件有 RTX 4060 8G，但当前 Python 环境安装的是 CPU wheel，**实际运行走 CPU**。
- 本地 bge-m3 首次索引：1465 条编码约 129.5s（CPU），落盘缓存后重跑直接读缓存。

### 8.3 检索评测（见 8.4 实测数字）

交付：`eval/run_retrieval_eval.py`

- 对齐来源：`LawAnswer\eval\qa_test_cases.jsonl`、`LexSynergy Legal QA System\eval\qa_set_100.jsonl`、
  `qa_set_130.jsonl`、`LexWeave\data\gold\gold_200.jsonl`。
- QA 的 `expected_articles` 按 `法名#条号` 映射；LexWeave 的 `law#数字` 先转中文条号再校验存在性。
- 一条问题所有标注条号都能在 1465 条语料中找到才纳入；空标注与无法对齐整条剔除并计数。
- 按 question 去重（100/130/40 为嵌套包含，LexWeave 独立）。
- 三档：dense、hybrid、hybrid+rerank；输出问题命中数与 gold 条号召回数，不设目标值。

### 8.4 阶段 2 实测结果

检索评测（单次运行；`torch 2.15.0+cpu`、`cuda_available=False`，**实际设备 CPU**）：

- 对齐：4 个文件分别 35/95/125/200 条；合并按 question 去重后 330 条，剔除 5 条空标注，纳入 **325** 条。
- gold 条号标注总数 **626**。
- dense：问题命中@5 325/325、条号召回 405/626（0.6470）；@10 468/626（0.7476）。
- hybrid：问题命中@5 325/325、条号召回 398/626（0.6358）；@10 471/626（0.7524）。
- hybrid+rerank：问题命中@5 325/325、条号召回 439/626（0.7013）；@10 501/626（0.8003）。
- 耗时（CPU）：索引缓存读取 13.35s；325 条查询编码 11.39s；dense 1.99s；hybrid 1.73s；
  hybrid+rerank 1000.88s（约 3.08s/题）。
- 问题命中饱和为 1.0000，只能说明每题至少命中一条 gold；多标注题的部分条号仍未召回，因此以条号召回为准。

回归（同一环境）：

- `pytest`：**22 passed in 4.29s**（原 15 个 + 新增 7 个）。
- 合成评测：条款召回 18/18；precision 18/22；溯源 44/44；三闸全过 38/44；调用 192；tokens 0/0。
- 示例评测：docx 4/4、pdf 4/5，findings 8/10，溯源 8/8 与 10/10，三闸 6/8 与 8/10。
- CLI mock 端到端仍可跑通；因 CPU 加载 bge-m3，单进程耗时 18–27s（BM25-only 时为 0.35s 级）。

边界：检索集是外部既有 QA/gold，题目与法条词汇重合较强；以上数字不可外推为真实合同审查效果。

### 8.5 容器化（阶段 2 补充）

交付：`Dockerfile`、`docker-compose.yml`、`docker-compose.gpu.yml`、`.dockerignore`。

- 镜像不含模型与数据；`./data`、`./artifacts`、`../模型`（只读）通过 bind mount 持久化。
- 环境变量统一：`LEXFLOW_MODEL_DIR=/models`、`LEXFLOW_DATA_DIR=/app/data` 等；本地与容器共用 `Settings`。
- GPU 通过 `docker-compose.gpu.yml` 覆盖层可选启用；无 GPU 时基础 compose 自动降 CPU。
- `docker compose config --quiet` 与 GPU 覆盖层配置校验均通过（exit 0）。
- 真实 `docker compose build` 未完成：Docker Hub registry 不可达（拉基础镜像超时），改用本机已有
  `python:3.12.14-slim-bookworm`；随后 CUDA 版 torch 依赖（nvidia-* 合计数 GB）下载极慢，
  构建在数十分钟内未完成。之后 Docker Desktop engine 退出（`npipe ... dockerDesktopLinuxEngine` 不可达），
  未能执行 `docker compose up` 冒烟，因此**没有容器内 `/health` 实测数据**。
- 改进：默认镜像改为 CPU 版 torch（`download.pytorch.org/whl/cpu`），镜像显著变小且无 GPU 也能起；
  GPU 通过 build arg `TORCH_INDEX=https://download.pytorch.org/whl/cu130` + GPU 覆盖层可选启用。
  该改动已完成，但同样未能在本机完成一次完整构建。

### 8.6 续做核对（2026-11-03 中断后）

- 语料：重跑构建，1465/1260/107/98 校验通过，两次 SHA256 相同；劳动法源文件 1122 字、正文替换 1018 字、
  影响 107 条、正文残留部首 0。
- 检索：三档结果与耗时均已在 CPU 上实测（见 8.4）；CSV 325 行，`retrieval_results.md` 已记录 device=cpu。
- 回归：`pytest` 22 passed；合成评测与示例评测指标与阶段 1 一致。
- Docker：`compose config` 两次校验均通过；后续 CPU 容器冒烟见 8.7。

### 8.7 GPU 与容器实测（2026-11-03 续做）

GPU：

- 宿主 `nvidia-smi`：驱动 610.47，CUDA UMD 13.3，RTX 4060 Laptop，8188MiB。
- 安装 `torch 2.14.1+cu130`（Windows wheel 1.99GB，下载约 2 分钟，安装 37s）。
- 验证：`cuda_available=True`、`device_count=1`、`get_device_name=0` 为 `NVIDIA GeForce RTX 4060 Laptop GPU`、
  显存 8187MB、2000×2000 矩阵乘通过。
- 检索三档在 GPU 重跑：召回数字与 CPU 完全一致；hybrid+rerank 从 1000.88s 降到 119.42s（约 0.367s/题）。
- reranker 单题：GPU 首题 0.982s（预热），随后 0.396s、0.412s；CPU 平均 3.08s。
- 口径补丁（2026-11 top-k 实验）：以上 GPU 数字均来自**直接调用 retriever/embedder 的脚本路径**
  （`eval/run_retrieval_eval.py`）。从 **service 链路** 调用 CUDA 编码时报 `cudaErrorMemoryAllocation`，
  向量层降级 `bm25-only`；直接调用 `LocalEmbedder` 仍正常。交付口径因此改为「GPU 仅限直接调用 embedder 的
  脚本路径，服务链路按 CPU 运行」，不能写成「GPU 不可用」或「GPU 一切正常」，详见 §10。

Docker：

- `docker version`/`docker info` 正常：context `desktop-linux`，Server 29.8.0，driver overlayfs。
- CPU 镜像构建成功：`lexflow:0.1.0`，当前 ID `d7696c3e0c23`，5.13GB。
- `docker compose up -d`：api/ui 均启动，api health 变 healthy。
- 本机 8000 在 Windows 排除端口段，冒烟用 `LEXFLOW_API_PORT=18000`、`LEXFLOW_UI_PORT=18501`。
- 容器 `/health`：1465 条、`retrieval_mode=hybrid`、`vector_backend=inline`、`embedding_device=cpu`。
- 容器 `/analyze`：mock docx，`findings=8`，`elapsed_ms=4237`，`retrieval_device=cpu`。
- GPU 容器：Docker 有 `nvidia` runtime，`nvidia-smi` 在 `--gpus all` 容器内可见；但 CUDA 镜像在
  `uv pip install --index-url .../cu130 torch` 失败（exit 2），未继续重试，GPU 容器 `/health` 未实测。

### 8.8 阶段 3 执行记录（2026-11-03）

交付：

- `lexflow/review/rules.py`：8 条显式规则（R-PAY / R-PEN / R-TERM / R-JUR / R-CONF / R-FM / R-LIAB / R-ACC），
  含规则 ID、条款类型、判定条件、命中后动作、法条依据、误报边界与句级 detectors/exclusions。
- `lexflow/review/schema.py`：Pydantic `FindingDraft` / `ReviewPayload` + 引用存在性校验。
- `lexflow/failures.py` + `scripts/failure_stats.py`：`failures.jsonl` 归档与统计。
- `lexflow/retrieval/hybrid.py`：索引状态机 `new/indexing/ready/bm25-only` + 后台加载线程 + 并发等待。
- `lexflow/synthetic.py`：v1/v2 数据剖面；v2 新增保密、不可抗力、责任限额、验收标准。
- 测试：`tests/test_rules.py`、`tests/test_failures.py`、`tests/test_retriever_concurrency.py`，并更新合成测试。

实测（真实命令输出）：

- `pytest`：**33 passed in 7.78s**。
- 预付款回归：设备采购模板交付条款不再命中付款规则；`RISK_CLAUSES["payment"]` 仍命中 `R-PAY-001` 且只命中一次。
- v1 同一数据集对照：precision **18/22 → 18/18**；召回 18/18 → 18/18；
  溯源 44/44 → 36/36；三闸全过 38/44 → 36/36。
- 示例合同：docx 4/4、pdf 4/5；pdf 曾因规则收紧降到 1/5，补回「千分之…违约金」、
  「单方所在地管辖」「验收标准由乙方单方确定」三类真实风险后恢复；仍漏的 1 条是合法救济条件。
- v2 扩展集：18 份合同、30 处注入；precision 30/30、召回 30/30、溯源 60/60、三闸全过 60/60、调用 432。
- v1 重新生成与阶段 1 落盘 gold 的 12 份文本 SHA 完全一致（profile 可复现）。
- 失败归档：人为造一条 mock 非法输出，`total=1`、`invalid_json=1`、`retryable=false`。
- `/health` 并发：首轮返回 `indexing/vector_ready=false`；8 个并发 `mode` 全部返回 `hybrid`，无一次 `bm25-only`。
- 容器：重新构建 `lexflow:0.1.0`（ID `9f0d4db80f63`，5.13GB）；首次 `/health` 为 `indexing/vector_ready=false`，
  首次请求后约 13s 转为 `hybrid/vector_ready=true/cache/cpu`；`/analyze` mock `findings_count=8`、`elapsed_ms=7498`、
  `retrieval_device=cpu`，8 条 finding 均带 `rule_id`；冒烟后已 `down`。

边界：

- v2 的 30/30 来自规则与注入刻意对齐，只证明规则表能覆盖这 8 类注入，不代表真实合同效果。
- 管辖规则只引用民法典第四百七十条（争议解决方法）；语料不含民诉法，不判断管辖实体效力。
- 真实 API 下的非法输出比例、失败归档增长与轮转策略尚未统计。

### 8.9 阶段 3 收尾修复记录（2026-11-03）

目标：修掉 `R-PEN-001` 对有上限违约金的误报，并把 `stance` 从调用链一路传到规则层；不回退样本，不进入阶段 4。

改动：

- `lexflow/review/rules.py`：
  - `R-PEN-001` 增加 `clause_exclusions`（累计不超过 / 最高不超过 / 上限为 / 不超过合同总价 / 封顶），
    只排除有上限表述，不弱化 detector；`不设上限 / 无上限 / 上不封顶 / 未封顶` 仍命中。
  - 新增 `R-PAY-002`「逾期付款免责」（民法典第五百七十七条），覆盖「甲方逾期付款不承担违约责任」。
  - `R-PAY-001`、`R-JUR-001`、`R-ACC-001`、`R-PAY-002` 增加 `stance_detectors`：
    甲方视角保持阶段 3 行为（单方条款均报）；乙方视角只报甲方/买方一侧条款，付款义务切换为乙方一侧。
  - `detect_rules(text, clause_type, stance="甲方")` 归一化 stance 并选择对应 detector/exclusion。
  - 规则表由 8 条增至 9 条，`RULE_COVERAGE` 同步。
- `lexflow/llm/mock.py`：解析 prompt 中的 `STANCE:` 并传给规则层。
- `lexflow/review/reviewer.py`：`review_clauses` 新增 `stance` 参数，user prompt 写入 `STANCE:`。
- `lexflow/graph/nodes.py`：`_review_node` 从图状态读取 `stance` 传入。
- CLI / API / Streamlit / eval 原本已把 `stance` 传入 `service.analyze`，本次只补齐下游链路，未在调用处写死。
- `tests/test_rules.py`：新增 7 个用例（有上限不报、无上限仍报、验收/管辖/付款方向切换、验收特权方向、逾期付款免责双向）。

实测（真实命令输出）：

- `uv run pytest -q`：**40 passed in 6.42s**。
- 示例合同：docx 4/4、findings 8；pdf 4/5、findings **8 → 10**；溯源 10/10、三闸 10/10。
- v1（甲方立场）：召回 18/18、precision 18/18、findings 36、溯源 36/36、三闸 36/36（与收尾前一致）。
- v2（甲方立场）：召回 30/30、precision 30/30、findings 60、溯源 60/60、三闸 60/60（与收尾前一致）。
- 乙方立场最小用例：乙方单方验收标准、乙方所在地管辖不报；甲方所在地管辖、甲方逾期付款免责仍报。

4/5 的可自证口径：

- 示例采购 PDF 的 4/5 = 原有 1 条 + 阶段 3 事后补的 3 条通用 detector；不是改样本，也不是 5/5。
- 反向模拟：撤销 `R-PEN-001` = 3/5；撤销 `R-ACC-001` = 3/5；撤销 `R-JUR-001` = 3/5；三条全撤 = 1/5；
  示例 DOCX 始终 4/4。
- 样本 SHA 前后一致、未被修改：
  PDF `065EE64FB062E82ACF1875337DC995A7AAF71774C8B7776BF7BB98AEA36CB478`（1940 B）；
  DOCX `A0745C7F4893109F6834D4E29D6FE9C5F3FE6477B9C7BF419B3F628D120B8E23`（37255 B）。
  `rules.py` 修改时间晚于样本创建时间。
- `R-PEN-001` 曾误报有上限违约金，已修；`R-JUR-001` / `R-ACC-001` 曾不读立场，已修。

边界：

- 乙方立场只有最小用例，没有独立 gold 集；甲方立场保留「单方条款均报」的不对称语义，用于保持既有评测可比。
- 示例采购 PDF 仍漏 1/5（「两次维修仍不能正常使用后可解除」），判定为合法救济条件，未为凑数改规则。

## 8.10 阶段 4：交付物与演示可用（已完成，2026-11-03）

目标：让交付物与演示可用，不新增审查能力。

改动：

- **定位锚与真实页码**
  - `lexflow/parse/parser.py`：PDF block 增加 `anchor=p.{page} ¶{block}` 与真实 `page_no`；docx 按非空段落 1-based 编号，章节标题识别 `第X章`，anchor 为 `§段落号 [章节标题]`，表格行为 `表n 行r`；OCR 行 anchor 为 `OCR 行n`。
  - `lexflow/segment/segmenter.py`：`_locate()` 从字符偏移反查 page/bbox/anchor；docx 不再计算页码，`page_no=None`；`Clause` 写入 `doc_id` 与 `locator="anchor [chars start:end]"`。
  - `lexflow/models.py`：`PageBlock` 加 `anchor/page_no`；`Clause.page_no` 可空，新增 `doc_id/locator`；`Finding` 新增 `doc_id/locator`；`Report` 新增 `doc_id`。
  - `lexflow/storage/db.py`：clauses / findings 增列；旧 clauses 表缺列时重建迁移。
  - `lexflow/report/docx_export.py`：风险表改为 7 列（条款 / 定位锚 / 风险等级 / 问题 / 原文 / 法条依据 / 建议），定位列显示 `doc_id · locator`；模板表头变化时自动重建模板。
  - 解析 warning 明确写「docx 无真实分页信息，page_no 置空，不按字符位置推断页码」。
- **HITL 中断/恢复**
  - 新增 `scripts/hitl_demo.py`：`start` 触发 `interrupt()` 并写 `artifacts/hitl_demo_state.json`；`resume` 新建 `AnalyzeService` 用 `Command(resume=...)` 继续。
  - `lexflow/graph/nodes.py`：`export_docx_node` 支持 `reject`，状态置 `rejected`、不导出 docx。
  - `lexflow/service.py`：resume 后重读 matter 状态，保证 reject 返回正确 status。
- **UI**
  - `lexflow/ui/app.py` 重写为单页：上传/立场/as_of/engine → 分析 → 风险表按 high/medium/low 排序（规则 ID、法条依据、定位锚）→ 确认/驳回 → 下载 docx → 显示调用次数/tokens/节点耗时/端到端耗时。
  - `engine=mock` 用红色 `st.error` 横幅，明确「固定规则表，不代表真实模型效果」。
- **日志与可观测**
  - `lexflow/logging.py`：统一必需字段 `matter_id/node/model/tokens/latency/status/device`，缺失补 `null`。
  - `lexflow/review/reviewer.py`：每条 clause 计时，`review.done` / `review.failed` 补齐字段。
  - `lexflow/cli.py`：新增 `timeline <matter_id>`，输出节点耗时与调用/token/检索模式。
  - `lexflow/service.py`：图返回后把完整 `node_timings`（含 `assemble_report`、`export_docx`）合并进 `report.metrics` 并持久化；`Store.update_matter` 支持 dict。

实测（真实命令输出）：

- `uv run pytest -q`：**45 passed, 1 warning in 7.35s**。
- 示例合同：docx 4/4、findings 8；pdf 4/5、findings 10；溯源与三闸均全过（与阶段 3 一致）。
- v1（甲方）：召回/precision 18/18、findings 36、溯源 36/36、三闸 36/36。
- v2（甲方）：召回/precision 30/30、findings 60、溯源 60/60、三闸 60/60、调用 432。
- HITL 两次进程：
  - start：pid 33092，`matter-9fb21253c4`，`status=awaiting_review`，findings 10，`docx_path=null`；
  - resume：pid 75288，`status=completed`，docx 38738 B，`new_process=true`。
- `cli timeline matter-9fb21253c4`：8 个节点耗时，`total_node_ms=1547`，`doc_id=065ee64fb062e82a`。
- Docker `/health` 冒烟（重建镜像，端口 18000）：`status=ok`，`articles=1465`。

边界：

- PDF 页码是「PDF 页序」（从 1 开始），不等于打印页码；docx 不推断页码。
- 示例采购 PDF 仍漏 1/5（合法解除救济条件），未为凑数改规则。
- 阶段 4 时容器只做了 `/health` 冒烟，未在容器内跑完整分析（阶段 5 已补 `/analyze`、`/review`、`/export`）。
- 阶段 4 时 GPU 容器尚未构建（阶段 5 已构建，但容器内 GPU 检索不可用，见第 9 节）。

## 9. 阶段 5 执行记录（已完成）

交付：

- `scripts/demo.py`：一条命令「上传示例 → mock 分析 → 风险清单 → 确认 → 导出 docx → 指标」，全程离线、无 API key。
- `docs/DEMO.md`：3–5 分钟演示脚本、追问应对与不可越界表述。
- `tests/test_api.py`：6 个用例覆盖 `/health`（含 `indexing → hybrid` 状态流转）、`/analyze`、`/review`、`/export`、跨 service 实例 resume、404/409。
- `lexflow/api/main.py`：service 改为延迟创建，测试可注入临时目录，不污染 `data/`。
- `Dockerfile`：支持 `docker/wheels/*.whl` 本地安装 torch，带 uv 缓存挂载。
- `docker-compose.gpu.yml`：`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`（未能绕过容器 OOM，见下）。
- README 按「快速开始 / 架构与节点 / 规则表 / 实测数字与来源 / Docker 与 GPU / 已知限制 / 未实现项」重排。

实测（真实命令输出）：

- `uv run pytest -q`：**51 passed, 1 warning in 9.79s**（阶段 5 当次；收尾复核为 56 passed，见 §10）。
- demo：`matter-9b25b21a2b`、8 findings、分析 17835 ms + 确认/导出 70 ms、docx 38564 B、节点 1677 ms、`hybrid/cuda`。
- 示例：docx 4/4 findings 8；pdf 4/5 findings 10。
- v1：18/18、findings 36；v2：30/30、findings 60。
- CLI：`matter-831b2b0fc7`、findings 8、14388 ms、`hybrid/cuda`、节点 1415 ms。
- GPU 容器：本地 cu130 wheel 构建成功，容器内 `torch.cuda.is_available()=True`；但 bge-m3 加载 OOM（Docker Desktop/WDDM 单次分配 640 MB 成功、768 MB 失败），`/health` 落 `bm25-only`、`embedding_device=null`，`/analyze` findings 8、`elapsed_ms=463`、`retrieval_device=n/a`。

结论：本机容器内无法启用 GPU 检索，交付形态改为**宿主机脚本路径 GPU + CPU 服务/容器**：宿主机只有直接调用
`LocalEmbedder` 的评测脚本路径实测 `cuda`，service 链路（含容器）按 CPU 运行；GPU 覆盖层与本地 wheel 路径保留，
不能宣称容器已启用 GPU。2026-11 top-k 实验进一步确认，宿主机 **service 链路** 调 CUDA 编码会
`cudaErrorMemoryAllocation` 降级 `bm25-only`，只有脚本路径可用（见 §10）。

## 10. top-k 对照实验与结论（2026-11，收尾）

**目标与设计（严格单变量）**：只改「每个条款检索候选法条数」这一个变量。A 组 `LEXFLOW_STATUTE_TOP_K=3`（默认），
B 组 `LEXFLOW_STATUTE_TOP_K=10`。6 份固定样本：`examples/sample_service_contract.docx`、
`examples/sample_procurement_contract.pdf`、`data/synthetic_v1/contracts/tech_0.docx` ~ `tech_3.docx`
（后 4 份为 v1 gold 前 4 条）。两组均 `engine=api`、`stance=甲方`、`LEXFLOW_EMBEDDING_DEVICE=cpu`；
开跑前预热检索，模式不是 `hybrid` 就中止，保证两组可比。因 service 链路 CUDA 编码报
`cudaErrorMemoryAllocation`，A/B 统一 CPU；一次 `bm25-only` 的 B 组空跑作废。

实现：`lexflow/config.py` 新增 `statute_top_k`（默认 3），经 `graph/build` → `graph/nodes._review_node` →
`review/reviewer.review_clauses` → `retriever.search` 全链路透传；除候选法条数外不改任何行为。

**A/B 对照表**：

| 指标 | A（top_k=3，默认） | B（top_k=10） | 变化 |
|---|---|---|---|
| findings 总数 | 87 | 88 | +1 |
| 带法条 findings | 20 | 51 | +31 |
| G1 通过 / 分母 | 20/87 | 49/88 | +29 |
| G2 通过 / 分母 | 17/87 | 48/88 | +31 |
| G3 通过 / 分母 | 20/87 | 49/88 | +29 |
| 三闸全过 / 分母 | **17/87** | **48/88** | +31 |
| v1 precision | 5/30 = 0.1667 | 5/30 = 0.1667 | 不变 |
| v1 recall | 5/6 = 0.8333 | 5/6 = 0.8333 | 不变 |
| prompt tokens | 64,062 | 106,186 | +42,124（+65.8%） |
| completion tokens | 112,355 | 134,312 | +21,957（+19.5%） |
| 成本 | ¥1.0270（1.026964） | ¥1.2869（1.286868） | +¥0.2599（+25.3%） |
| 6 份总耗时 | 513.7s（513,729 ms） | 628.1s（628,051 ms） | +114.3s（+22.3%） |

> **分母口径**：三闸「通过 / 分母」的分母是**该组全部 findings**（A=87、B=88），不是带法条条数，也不是
> 条款数。未带法条的 finding 三闸必然不通过，所以 `17/87 → 48/88` 同时包含「没有引用」和「引用了但校验失败」
> 两类。G1/G2/G3 的分母同样都是全部 findings。A 组 G1 通过数与带法条数相等（20/20）；B 组为
> 49/51，差的 2 条是证据账本中没有对应条号（见本节末）。

- v1 口径（4 份 `tech_*.docx`，注入 gold 共 6 个条号）：precision = 命中条号数 / 实际报出的不同条号数
  （A/B 均 5/30）；recall = 命中条号数 / 注入 gold 条号数（A/B 均 5/6）；溯源 A 57/57、B 61/61（均 100%）。
  **precision 与 recall 两组完全不变**。
- 数据库写入一致：A 87/87、B 88/88（每个 matter 的 DB findings 数 = 报告 findings 数）。

**5 条新增引用抽检（B 组 41 条新增引用中人工挑 5 条，覆盖 4 个文件）**：

| # | 文件 / 条款 | 条款摘要 | 新增引用 | 适用性判断 |
|---|---|---|---|---|
| 1 | `sample_service_contract.docx` C0003 | 三日内验收，逾期未异议视为合格 | 民法典第六百二十二条 | **跨编类推**：622 属买卖合同编「检验期限过短」规则，用于服务合同验收只能类推 |
| 2 | `sample_procurement_contract.pdf` C0005 | 日千分之三违约金，未设上限 | 民法典第五百八十五条 | **适用**：合同编违约责任通则 |
| 3 | `tech_3.docx` C0007 | 保密期限为合同终止后两年 | 民法典第五百五十八条 | **适用**：合同编通则的后合同义务（含保密） |
| 4 | `sample_service_contract.docx` C0006 | 争议由甲方所在地法院管辖 | 中华人民共和国劳动法第七十九条 | **明显错位**：服务合同管辖引劳动法劳动争议调解仲裁条文；模型的「若本合同属于劳动合同」前提不成立 |
| 5 | `tech_1.docx` C0002 | 按月对账、次月十五日前结算 | 民法典第六百二十八条 | **明显错位**：628 属买卖合同编价款支付时间，跨合同类型误引 |

小计：2 条适用、1 条跨编类推、2 条明显错位（劳动法 79、民法典 628）。

**核心限制**：**三闸不校验适用性，top-k 扩召回会让「条号对但法条错」的绝对数量上升。** G1 只校验条号存在、
法名与条号一致及证据账本；G2 只校验引文逐字一致；G3 只校验 as-of 时效。一条法条可以三闸全过却在法律上与
条款无关。B 组三闸全过绝对数 17→48、带法条数 20→51，但抽检 5 条中 2 条明显错位，说明闸门通过率不是检测质量。

**结论：默认 `top_k` 保持 3**，10 只作为可选开关 `LEXFLOW_STATUTE_TOP_K=10`。理由：

1. **检测质量未提升**：v1 precision 5/30、recall 5/6 两组完全相同，findings 仅 87→88（+1）。
2. **成本 +25.3%**：¥1.0270 → ¥1.2869，prompt token +65.8%。
3. **抽检适用性差**：5 条新增引用中 2 条明显错位、1 条跨编类推，而三闸无法拦截。

默认行为复核：不设置 `LEXFLOW_STATUTE_TOP_K` 时 `Settings().statute_top_k == 3`（2026-11-04 实测），与 A 组一致。
收尾复核：`uv run pytest -q` → **56 passed, 1 warning in 12.52s**（2026-11-04）。

**成本合计**：有效 A+B = ¥2.3138（1.026964 + 1.286868）；作废的 `bm25-only` B 组空跑 ¥1.3106；
累计 ¥3.6244。作废那次的 6 个 matter 仍在 DB 中，已从结论中排除。

其他偏差：B 组有 2 条带法条 finding 的 G1 不通过（`sample_service_contract.docx` C0002 引民法典第八百八十二条、
`tech_2.docx` C0007 引民法典第五百五十八条，均为「证据账本中不存在」），扩召回不能保证证据账本覆盖。

### 10.1 本次收尾的已知限制

- **G4（语义适用性）未做**：G1/G2/G3 不校验法条是否适用于该条款；「条号对但法条错」可以三闸全过，
  top-k 扩召回会提高这类错误的绝对数量。
- **gold 只标注入风险**：v1/v2 gold 由注入规则反推，只覆盖注入风险，不是律师标注，不覆盖模板中其他不利条款。
- **precision 口径偏差**：v1 precision = 命中条数 / 实际报出的不同法条条数，漏报条款不进分母；
  它是链路一致性指标，不是法律准确率。
- **按条款类型预过滤未做**：只对照了候选法条数量（3 vs 10），没有按条款类型先过滤候选法条。


