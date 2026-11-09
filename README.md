# lexflow M0：合同审查最小闭环

一个可运行的合同审查多智能体最小工程（M0）。主推理可走 OpenAI 兼容 API（DeepSeek/Qwen 均可），**没有 API key 时整条链路用确定性 mock 跑通**，所有产物显式标注 `engine=mock`。

> 本项目是工程原型，**不是法律意见**。仓库内**没有任何实测的法律效果准确率、通过率或线上指标**；`eval` 只输出本次运行的原始计数与比率（如条款数、召回率），mock 引擎下的数字只反映链路是否跑通，不代表模型能力。
>
> 每个数字的来源与性质见 [§4 实测数字与来源](#4-实测数字与来源)。凡是「合成数据 / mock / 单次运行」的结论，都不能外推到真实合同或真实模型。

---

## 1. 快速开始

要求：Python >= 3.11，已安装 [uv](https://docs.astral.sh/uv/)。

```powershell
# 安装依赖（含 dev 依赖）
uv sync

# 可选：本地 embedding（未安装时自动退化为 BM25-only）
uv sync --extra local-embed

# 可选：扫描件 OCR（未安装时遇到扫描件会抛明确错误）
uv sync --extra ocr
```

### 1.1 一条命令演示（推荐先跑这个）

```powershell
uv run python scripts/demo.py
```

全程离线、不需要 API key，依次完成：上传示例 → mock 分析 → 风险清单（按等级排序，含规则 ID、法条依据、定位锚）→ 人工确认 → 导出 docx → 打印耗时 / token / 节点统计。
演示脚本与 CLI/API/UI 共用 `AnalyzeService`；3–5 分钟讲法与追问应对见 [docs/DEMO.md](docs/DEMO.md)。

变体：

```powershell
uv run python scripts/demo.py --file examples/sample_procurement_contract.pdf --stance 乙方
uv run python scripts/demo.py --show-logs          # 保留 JSON 日志，便于讲可观测
$env:LEXFLOW_EMBEDDING_ENABLED="false"; uv run python scripts/demo.py   # 强制 BM25-only，启动更快
```

### 1.2 CLI（无 API key 端到端）

```powershell
# 交互模式：默认在导出前 interrupt() 等待单人确认
uv run python -m lexflow.cli analyze examples/sample_service_contract.docx --engine mock

# 自动化模式：跳过交互确认
uv run python -m lexflow.cli analyze examples/sample_service_contract.docx --engine mock --auto-approve
uv run python -m lexflow.cli analyze examples/sample_procurement_contract.pdf --engine mock --auto-approve

# 按 matter_id 查看本次分析的节点耗时 / token / 检索模式
uv run python -m lexflow.cli timeline matter-xxxxxxxxxx
```

### 1.3 HITL 中断 / 恢复（跨进程）

```powershell
# 第 1 次进程：interrupt 停在 awaiting_review，写出 artifacts/hitl_demo_state.json
uv run python scripts/hitl_demo.py start

# 第 2 次进程：新 AnalyzeService 从 SqliteSaver checkpoint 恢复并导出 docx
uv run python scripts/hitl_demo.py resume
# 可选：resume --action reject 验证驳回不导出
```

### 1.4 API + UI

```powershell
# Docker（数据/模型挂载持久化，详见第 5 节）
docker compose up --build -d

# 或本地一键启动（新窗口分别启动 API 与 Streamlit）
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1

# 或分开启动
uv run lexflow-api                     # http://127.0.0.1:8000/docs
uv run streamlit run lexflow/ui/app.py # http://127.0.0.1:8501
```

接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 引擎默认值、语料模式、法条数、检索模式（可能为 `indexing`）、`vector_ready`、向量后端/原因、embedding 设备、reranker 开关 |
| POST | `/analyze` | 上传 PDF/docx，返回 `matter_id` 与风险清单（停在确认点） |
| POST | `/review/{matter_id}` | 单人确认，恢复 `interrupt()` 并导出 docx |
| GET | `/export/{matter_id}` | 下载 docx |

Streamlit UI 为单页演示：上传合同 → 选立场/as_of/engine → 审查 → 风险表（按风险等级排序，含规则 ID、法条依据、定位锚）→ 人工确认/驳回 → 下载 docx，并展示本次 LLM 调用次数、tokens、节点耗时与端到端耗时。`engine=mock` 时页面顶部显示红色横幅，明确这是固定规则演示、不是真实模型效果。

### 1.5 测试与评测

```powershell
uv run pytest -q
uv run python scripts/build_corpus.py          # 重建 1465 条统一语料（幂等）
uv run python eval/run_eval.py
uv run python scripts/gen_synthetic_contracts.py
uv run python eval/run_synthetic_eval.py
uv run python eval/run_retrieval_eval.py       # 三档检索评测（dense / hybrid / hybrid+rerank）
```

### 1.6 mock 与真实 API 的区别

| 维度 | `engine=mock` | `engine=api` |
|---|---|---|
| 推理 | `lexflow/llm/mock.py` 中的确定性规则匹配，输出固定文本 | `lexflow/llm/openai_compat.py`，OpenAI 兼容协议 |
| token | 恒为 0（不调用模型） | 记录 prompt/completion token 原始计数 |
| 成本 | 0 | 仅当配置单价时估算，否则留空 |
| 产物标注 | 报告与 docx 中标注 `MOCK 演示`、`engine=mock` | 标注 `engine=api` 与模型名 |
| 用途 | 验证链路、回归测试、无 key 演示 | 真实抽取与审查（**效果未评测**） |

`engine=auto`（默认）：有 `LEXFLOW_API_KEY` 走 API，否则自动落到 mock。

### 1.7 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LEXFLOW_API_KEY` | 空 | 为空时只能 mock |
| `LEXFLOW_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容地址 |
| `LEXFLOW_MODEL` | `deepseek-chat` | 模型名 |
| `LEXFLOW_PRICE_INPUT_PER_M` | 空 | 输入单价（元/百万 token），不填不估算 |
| `LEXFLOW_PRICE_OUTPUT_PER_M` | 空 | 输出单价（元/百万 token） |
| `LEXFLOW_EMBEDDING_MODEL` | `$LEXFLOW_MODEL_DIR/bge-m3`（存在时）；否则 `BAAI/bge-small-zh-v1.5` | 本地 embedding 路径/模型名 |
| `LEXFLOW_MODEL_DIR` | 项目父目录的 `模型`（本机即 `E:\project\模型`） | 模型根目录；容器内为 `/models` |
| `LEXFLOW_DEVICE` | `auto` | 统一设备解析：`cuda` 可用则用，否则 `cpu`；embedding/reranker 都继承 |
| `LEXFLOW_EMBEDDING_ENABLED` | `true` | 关闭后直接 BM25-only |
| `LEXFLOW_EMBEDDING_DEVICE` | 继承 `LEXFLOW_DEVICE` | 单独覆盖 embedding 设备；服务链路建议显式设为 `cpu`（见 [§2.6](#26-检索与降级)） |
| `LEXFLOW_STATUTE_TOP_K` | `3` | 每个条款检索的候选法条数；默认 3，`10` 仅作对照实验的可选开关（见 [§4.5](#45-top-k-对照实验与结论2026-11)） |
| `LEXFLOW_EMBED_CACHE` | `data/embeddings` | 向量缓存目录 |
| `LEXFLOW_RERANKER_ENABLED` | `false` | 默认关闭；评测脚本内部按需加载 |
| `LEXFLOW_RERANKER_MODEL` | `$LEXFLOW_MODEL_DIR/bge-reranker-v2-m3`（存在时） | 本地 reranker 路径 |
| `LEXFLOW_RERANKER_DEVICE` | 继承 `LEXFLOW_DEVICE` | 单独覆盖 reranker 设备 |
| `LEXFLOW_DATA_DIR` / `LEXFLOW_ARTIFACTS_DIR` / `LEXFLOW_DB` | `data` / `artifacts` / `data/lexflow.db` | 路径 |
| `LEXFLOW_CORPUS` | `data/corpus/law_articles.jsonl` | 法条语料 |
| `LEXFLOW_FAILURES` | `data/failures.jsonl` | 失败归档路径 |
| `LEXFLOW_API_URL` | `http://127.0.0.1:8000` | Streamlit 访问的 API 地址 |
| `LEXFLOW_LOG_LEVEL` | `INFO` | JSON 行日志级别；demo 默认压到 `ERROR`，可 `--show-logs` 恢复 |

项目根目录的 `.env` 会自动加载（已存在的系统环境变量优先，不会被覆盖；Docker Compose 也会读取同一文件做变量替换）。可直接复制 [.env.example](.env.example) 后填 key。`.env` 已在 `.gitignore` 与 `.dockerignore` 中排除，不会提交或打进镜像。

真实 API 示例（阿里云百炼 / DashScope，OpenAI 兼容端点）：

```powershell
# 复制 .env.example 为 .env，填入真实 key；或临时设置环境变量
$env:LEXFLOW_API_KEY = "sk-..."
$env:LEXFLOW_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:LEXFLOW_MODEL = "deepseek-v4.1-flash"
$env:LEXFLOW_PRICE_INPUT_PER_M = "2"
$env:LEXFLOW_PRICE_OUTPUT_PER_M = "8"
uv run python -m lexflow.cli analyze examples/sample_service_contract.docx --engine api --auto-approve
```

- 百炼北京地域的共享域名 `https://dashscope.aliyuncs.com/compatible-mode/v1` 仍可正常使用；生产环境可换成业务空间专属域名 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。
- `deepseek-v4.1-flash` 是百炼正式模型 ID（2026-10-26 文档），也可用快照版本 `deepseek-v4.1-flash-1017`。
- 单价（元/百万 token）：闲时（东八区 22:00–次日 08:00）输入 1 / 输出 4；忙时输入 2 / 输出 8。上例按忙时上限填，属于保守估算。

> 注意：真实 API 模式下模型输出的可靠性、条款召回与引用准确率**尚未评测**，不能把 mock 的比率当作 API 效果。

---

## 2. 架构与节点

### 2.1 LangGraph 主图

```
START → parse → segment_clauses → ┬ review_legal
                                  └ review_commercial   （并行）
      → bind_evidence → verify → assemble_report → export_docx → END
                                export_docx 内 interrupt() 单人确认
```

- checkpointer：`SqliteSaver`，与业务表**同一个 SQLite 文件、不同表**；中断后换进程仍可按 `matter_id` 恢复。
- 证据 reducer 按 `law_name#article_no` 去重，保留更高分；findings 按 `finding_id` 去重。
- `assemble_report` / `export_docx` 的耗能在图返回后由 service 写回，避免中断恢复后 metrics 缺尾节点。

### 2.2 目录结构

```
lexflow/
├── pyproject.toml / uv.lock
├── Dockerfile                   # 依赖 + 代码 + 模板；不含模型与数据
├── docker-compose.yml           # api + ui；./data、./artifacts、../模型 挂载
├── docker-compose.gpu.yml       # 可选 GPU 覆盖层（gpus: all + cu130 build arg）
├── docker/wheels/               # 可选：宿主机预下载的 CUDA torch wheel（*.whl 不入库）
├── .dockerignore
├── README.md
├── docs/PLAN.md                 # 盘点结论、数据替代策略、阶段计划与执行记录
├── docs/DEMO.md                 # 3–5 分钟演示脚本与追问应对
├── scripts/
│   ├── demo.py                  # 一条命令离线演示（mock）
│   ├── hitl_demo.py             # 跨进程 interrupt / resume 演示
│   ├── build_corpus.py          # 构建 1465 条统一语料
│   ├── make_samples.py          # 生成两份自造示例合同
│   ├── gen_synthetic_contracts.py # 生成合成合同 + 注入式 gold
│   └── dev.ps1                  # 一键启动 API + Streamlit
├── data/
│   ├── corpus/law_articles.jsonl # 1465 条法条（含 law_id/chapter_path/content_hash）
│   ├── corpus/labor_law_normalization.jsonl # 劳动法 107 条归一化前后记录
│   ├── corpus/corpus_manifest.json # 来源、条数、缺失元数据、文件 SHA256
│   ├── embeddings/              # bge-m3 向量缓存（运行后生成）
│   ├── synthetic/               # 合成合同、gold、manifest（生成后）
│   └── lexflow.db               # SQLite（业务表 + checkpoint 表，运行时生成）
├── assets/templates/risk_report.docx  # docxtpl 模板（自动生成）
├── artifacts/<matter_id>/       # 原文件副本与 report.docx（运行时生成）
├── examples/
│   ├── sample_service_contract.docx
│   └── sample_procurement_contract.pdf
├── eval/
│   ├── gold.jsonl               # 2 条自造标注
│   ├── run_eval.py              # 示例合同评测
│   ├── run_synthetic_eval.py    # 合成数据集评测（条款级 recall/precision）
│   ├── run_retrieval_eval.py    # 三档检索评测（对齐/剔除 + Recall@5/10）
│   └── results.* / synthetic_results.* / retrieval_results.* # 运行后生成
├── lexflow/
│   ├── config.py                # Settings（环境变量、模型目录）
│   ├── devices.py               # 统一 device 解析（cuda→cpu）
│   ├── logging.py               # JSON 行日志
│   ├── models.py                # Pydantic 模型
│   ├── corpus_build.py          # 1465 条语料构建 + 康熙部首归一化
│   ├── service.py               # CLI/API/UI/eval 共用服务层
│   ├── synthetic.py             # 合成合同生成器（固定种子 + 风险注入）
│   ├── cli.py
│   ├── api/main.py              # FastAPI（service 延迟创建，测试可注入临时目录）
│   ├── ui/app.py                # Streamlit 单页
│   ├── parse/                   # PyMuPDF / pdfplumber / python-docx / 可选 OCR
│   ├── segment/segmenter.py     # 条款切分 + 类型判定（含 char/page/bbox）
│   ├── retrieval/               # corpus / bm25 / embedder / reranker / hybrid
│   ├── llm/                     # base / mock / openai_compat / factory
│   ├── review/reviewer.py       # 法务 ‖ 商务双路审查
│   ├── verify/gates.py          # G1–G5 纯函数
│   ├── storage/db.py            # SQLite 业务表
│   ├── graph/                   # state / nodes / build（LangGraph 主图）
│   └── report/docx_export.py    # docxtpl 导出
└── tests/
    ├── conftest.py
    ├── test_segment.py          # 字符偏移不越界、PDF 保留 page/bbox
    ├── test_state_reducer.py    # 证据去重 reducer
    ├── test_gates.py            # 伪造条号 / 篡改引文 / 过期条文
    ├── test_e2e_mock.py         # mock 端到端、docx 行数 = DB findings、interrupt/resume
    ├── test_api.py              # /health 状态流转、/analyze、/review、/export、API 层 resume
    ├── test_synthetic.py        # 合成数据可复现、gold 一一对应、端到端
    └── test_corpus.py           # 1465/1260/107/98、哈希、归一化、幂等、降级
```

### 2.3 定位锚与页码（docx 不伪造页码）

- **PDF**：`parse` 用 PyMuPDF `page.get_text("blocks")` 取真实 `page_no` 与 `bbox`；条款的 `page_no` 直接来自该块所在页，不再按字符位置近似。
- **docx**：docx 文件格式本身不保存分页信息（分页由 Word 排版引擎在打开时决定，python-docx 读不到），因此**不推断页码**：`page_no` 置空，`Clause` / `Finding` 使用定位锚 `§段落号 [章节标题] [chars start:end]`（例如 `§3 第三章 违约责任 [chars 120:180]`），表格行使用 `表n 行r`。
- 风险项与证据引用可回溯：`doc_id + locator + original_quote`；导出 docx 的「定位锚」列显示 `doc_id · locator`。
- 解析时在 warnings 明确写入「docx 无真实分页信息，page_no 置空，不按字符位置推断页码…」，避免把页码当成真实版面信息。

### 2.4 HITL 与可观测

- 中断点位于 `export_docx` 之前；`scripts/hitl_demo.py start` 只跑到 `awaiting_review`，`resume` 是**新进程 + 新 `AnalyzeService`**，从 SQLite checkpoint 恢复并继续导出。
- stdout 每行一条 JSON；必需字段：`matter_id / node / model / tokens / latency / status / device`，缺失字段补 `null` 而不是省略。
- 事件示例：解析/切分/审查/证据/核验/组装/导出各 `node.done`，审查另有 `review.done`（每条 clause 一条）。
- 同一次分析的所有事件都带同一个 `matter_id`，可直接 `grep` 串联；`uv run python -m lexflow.cli timeline <matter_id>` 汇总节点耗时与 token。

### 2.5 失败归档

- 路径：`data/failures.jsonl`（可用 `LEXFLOW_FAILURES` 覆盖，容器内同 `./data` 挂载）。
- 字段：`matter_id / node / reason / raw_output_hash / retriable / attempt`，另附 `category / created_at`。
- 非法 JSON、缺字段、额外字段、法名/条号只给一半、条号不在语料中，都会被拒绝并落盘；不补默认值、不静默丢弃。
- `retriable` 对 API 引擎为 `true`，对确定性 mock 为 `false`。
- 统计：`uv run python scripts/failure_stats.py --path data/failures.jsonl`。

### 2.6 检索与降级

- 法条库：`data/corpus/law_articles.jsonl`，1465 条；字段
  `law_id / law_name / article_no / chapter_path / text / effective_from / effective_to / source_url / version_id / content_hash`。
- 混合检索：中文 2-gram BM25 + 本地 bge-m3 dense 向量 + RRF 融合；可选 bge-reranker-v2-m3。
- 向量默认指向 `$LEXFLOW_MODEL_DIR/bge-m3`；`sentence-transformers` 未安装、路径不存在或加载失败时自动退化为 `bm25-only`，不崩溃，并在 `/health` 的 `vector_reason` 与 `retrieval_mode` 中显示原因。
- 向量缓存：`data/embeddings/<sha256(model+corpus)>.npy/.json`；重跑直接读缓存，不重复编码。
- **首次索引并发语义**：加载在后台线程中完成，状态为 `new / indexing / ready / bm25-only`。`/health` 在构建中返回 `retrieval_mode=indexing`、`vector_ready=false`，**不会把正在建索引谎报成 bm25-only**；`mode`/`search` 会等待同一后台线程结束后再返回最终模式。API 层测试覆盖 `indexing → hybrid` 状态流转。
- **统一设备解析层** `lexflow/devices.py`：`LEXFLOW_DEVICE=auto` 时 `cuda` 可用则用，否则 `cpu`；embedding 与 reranker 共用。实际设备写入 JSON 日志 `retrieval_device`、`/health` 的 `embedding_device`、报告 `metrics.retrieval_device` 与检索评测结果。
- 本机硬件：`torch 2.14.1+cu130`，`torch.cuda.is_available()=True`，`device_count=1`，设备名 `NVIDIA GeForce RTX 4060 Laptop GPU`，显存 **8187 MB**；驱动 610.47 / CUDA UMD 13.3。
- **GPU 可用性有分叉（2026-11 top-k 实验实测）**：直接调用 `LocalEmbedder` 的脚本路径（如 `eval/run_retrieval_eval.py`）可在 `cuda` 上正常编码，三档检索召回与 CPU 完全一致；但从 **service 链路**（`AnalyzeService` → 图节点 → 检索器）调用 CUDA 编码时报 `cudaErrorMemoryAllocation`，向量层降级为 `bm25-only`。因此交付口径是：**GPU 仅保证直接调用 embedder 的脚本路径，服务链路按 CPU 运行**（`LEXFLOW_EMBEDDING_DEVICE=cpu`）。不能写成「GPU 不可用」，也不能写成「GPU 一切正常」。

### 2.7 法条语料构建

- 脚本：`uv run python scripts/build_corpus.py`（默认输出 `data/corpus/`）
- 来源：
  - 民法典 / 劳动合同法正文：`E:\project\LexSynergy Legal QA System\data\corpus\articles.jsonl`
    （该文件由 `E:\project\LawAnswer-doubao\data\raw\*.md` 抽取）
  - 劳动法正文：`E:\project\LawAnswer-doubao\data\raw\中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md`
- 条数校验：1260 / 107 / 98，总计 1465；`(law_id, article_no)` 唯一。
- 幂等：连续两次构建输出 `law_articles.jsonl` 的 SHA256 相同（实测
  `cc76c77435b06a1059070ccc7c4516e3cbef9261fb9a139cd365df61638db642`）；构建过程不写时间戳。
- 劳动法康熙部首归一化：源文件共 **1122** 个康熙部首/CJK 部首补充区字符，正文替换 **1018** 个，影响 **107** 条；
  U+2F00–U+2FDF 用 NFKC；补充区 U+2E80–U+2EFF 的 4 个字符（⺠/⻔/⻓/⻅）显式映射；
  逐条记录在 `data/corpus/labor_law_normalization.jsonl`（107 行）；构建后正文部首残留为 **0**。
- 文本清理（各一处）：民法典第一千二百六十条去掉尾部“新华社…”；劳动合同法第九十八条去掉“八次会议通过）”。

| 字段 | 范围 | 处理 | 原因 |
|---|---|---|---|
| `effective_to` | 全部 | 留空 | 源文件未提供废止/失效日期，不编造 |
| `source_url` | 劳动合同法 | 留空 | 本地 Markdown 未包含来源 URL |
| `effective_from`（2018 修正生效日） | 劳动法 | 填 `1995-01-01` | 仅第一百零七条明示 1995-01-01；2018 修正案的生效日源文件未明示 |
| `effective_from`（现行文本） | 民法典 / 劳动合同法 | `2021-01-01` / `2008-01-01` | 源文件明示“自…起施行”，构建时正则校验后才写入 |

- 若外部文件缺失，`lexflow/retrieval/corpus.py` 的内置 `FALLBACK_ARTICLES` 仅含 5 条样例法条；此时 `/health` 的 `corpus_mode` 为 `fallback`，**该样例集不是完整法律库**。

---

## 3. 规则表

mock 的风险判定是显式规则表（代码同源：`lexflow/review/rules.py`）。每条规则都有规则 ID、条款类型、判定条件、命中后动作、法条依据与误报边界：

| 规则 ID | 风险类型 | 条款类型 | 判定条件 | 命中后动作 | 法条依据 | 误报边界 |
|---|---|---|---|---|---|---|
| R-PAY-001 | 付款与交付顺序 | payment/delivery | 付款义务指向当前立场一方（甲方视角=甲方/买方一侧，乙方视角=乙方/卖方一侧），且未与验收/交付绑定，或签约后短期全额付款 | 提示绑定验收与先履行抗辩 | 民法典 第五百二十六条 | 仅当前立场的付款义务；对方收款、收到预付款、已付款、付款后交付不命中 |
| R-PAY-002 | 逾期付款免责 | payment/liability/delivery | 付款方逾期付款不承担违约责任或免于赔偿 | 提示恢复逾期付款责任与资金占用损失 | 民法典 第五百七十七条 | 约定按日计违约金/资金占用费的不命中；乙方立场只报甲方一侧的免责 |
| R-PEN-001 | 违约金上限 | liability | 违约金按日/千分之计且未设上限（「千分之…违约金」两种顺序） | 提示设置累计上限 | 民法典 第五百八十五条 | 条款写明累计上限/封顶/不超过合同总价一定比例的不命中；只约定直接损失赔偿、无违约金表述不命中 |
| R-TERM-001 | 解除条件 | termination | 无理由/随时/单方解除，或解除后不承担赔偿 | 提示限定解除条件与善后责任 | 民法典 第五百六十三条 | 以严重违约且经催告为前提的解除不命中 |
| R-JUR-001 | 管辖 | jurisdiction | 约定由一方单方所在地法院/仲裁且无中立选项；甲方视角单方条款均报，乙方视角只报甲方一侧 | 提示核对管辖明确性与对等性 | 民法典 第四百七十条 | 仅指「争议解决方法」条款；乙方立场下乙方单方所在地管辖不报；本地语料无民诉法，不判断管辖实体效力 |
| R-CONF-001 | 保密 | confidentiality | 永久/无限期保密、已公开信息纳入保密、排除返还/销毁 | 提示限定范围、期限与期满处理 | 民法典 第五百零一条 | 约定合理期限（如终止后 2–3 年）不命中 |
| R-FM-001 | 不可抗力 | force_majeure | 排除不可抗力免责，或不可抗力下仍继续履行全部义务 | 提示恢复法定免责与顺延/解除 | 民法典 第五百九十条 | 约定通知、证明与协商延期不命中 |
| R-LIAB-001 | 责任限额 | liability/delivery | 无限赔偿、不设赔偿上限、覆盖间接/利润损失 | 提示设置赔偿上限并排除间接损失 | 民法典 第五百八十四条 | 约定「以合同总金额为限」不命中 |
| R-ACC-001 | 验收标准 | delivery | 任何理由拒绝验收、拒绝验收不承担违约后果，或验收标准由单方确定；乙方视角只报甲方一侧的单方标准 | 提示写明标准、期限与逾期视为通过 | 民法典 第五百一十一条 | 乙方立场下乙方单方标准不报；按附件技术标准验收、逾期视为通过不命中 |

排除项在**句级**生效，所以同一句里出现「预付款」不会再把整条放过；违约金上限例外：只要**整条条款**写明累计上限/封顶，就在条款级排除（`clause_exclusions`），不会因为「千分之」等计算方式而误报。付款规则同时要求付款动作指向当前立场一方；示例采购模板的「乙方应在收到预付款后交付」已由回归测试锁定为**不命中**。

### 3.1 示例合同 4/5 的来源、反向模拟与样本完整性

- 示例采购 PDF 的 4/5 = **原有 1 条**（设备采购付款条款）+ **阶段 3 事后补的 3 条通用 detector**（「千分之…违约金」、单方所在地管辖、验收标准由单方确定）；不是 5/5，也不是靠改样本得到。
- 反向模拟（同一份 PDF、同一套 gold，只撤销单条规则）：

| 撤销的规则 | 风险条款召回 |
|---|---|
| 撤销 R-PEN-001 | 3/5 |
| 撤销 R-ACC-001 | 3/5 |
| 撤销 R-JUR-001 | 3/5 |
| 三条全撤 | 1/5 |

  示例 DOCX（服务合同）在上述任一口径下始终为 4/4。
- 样本未被修改：PDF `SHA256=065EE64FB062E82ACF1875337DC995A7AAF71774C8B7776BF7BB98AEA36CB478`（1940 B），DOCX `SHA256=A0745C7F4893109F6834D4E29D6FE9C5F3FE6477B9C7BF419B3F628D120B8E23`（37255 B）；`lexflow/review/rules.py` 的修改时间晚于样本，样本与生成脚本哈希均未变化。
- 修复记录：`R-PEN-001` 曾把「累计不超过合同总价百分之五」的有上限违约金误报，已改为条款级上限排除；`R-JUR-001` / `R-ACC-001` 曾不读立场，乙方立场会把有利于乙方的单方条款报成风险，已改为 stance-aware。`stance` 由 CLI/API/UI → `service.analyze` → 图状态 → `review_clauses` → mock 规则层全链路传递，不在调用处写死。

### 3.2 两路审查的合并

法务、商务两路各自调用同一张 mock 规则表，同一条款会被两路各报一次。**合并已下沉到 reducer**（`lexflow/graph/state.py: merge_findings`），按 `(clause_id, risk_type)` 合成一条：

- 主条目取风险等级最高者，同等级时法务优先；
- `reviewers` 记录两路来源（如 `["legal","commercial"]`）；
- 两路 issue / suggestion 表述不同时，按 `[法务]` / `[商务]` 前缀拼接，两边的说法都保留；
- `risk_type` 为空或 `other` 时不合并——没有可靠的类型键，强行合并会把两个不同问题压成一条。

**为什么放在 reducer 而不是节点里**：节点返回的是「合并后的完整列表」，但 reducer 只做并集，没被覆盖到的旧条目（另一路的原始 `finding_id`）会被 union 回来，合并结果当场被撤销。这个 bug 在 2026-11-07 之前一直存在——合并函数写了、也在 `bind_evidence` 里调了，但 findings 始终是 8 条，且没有任何测试覆盖它。放到 reducer 里，两路写进来那一刻就合并，不存在中间态。

示例 DOCX 现在是 **4 条 findings 对应 4 个风险点**，不再是 8 条。

---

## 4. 实测数字与来源

以下均为**本机实际运行测得**，仅代表这一次运行；每条都给出可复跑命令。

> **口径变更（2026-11-07）**：`merge_findings` reducer 已做跨路合并，同一 `(clause_id, risk_type)` 只保留一条，findings 数约为旧口径的一半。带 `matter_id` 的历史记录保留**合并前**的原值以便对照；「示例 DOCX / PDF 评测」两行已按新口径重跑。

### 4.1 最终回归数字表

| 项目 | 实测结果 | 命令 / 来源 | 性质 |
|---|---|---|---|
| 单元/集成测试 | `62 passed, 1 warning` | `uv run pytest -q`（10.23s，2026-11-07 复核） | 真实执行 |
| 离线演示（热缓存） | `matter-9b25b21a2b`；8 findings（合并前口径）；分析 17835 ms + 确认/导出 70 ms；docx 38564 B；节点合计 1677 ms；`hybrid/cuda`；`llm_calls=14`、tokens 0/0 | `uv run python scripts/demo.py` | mock + 合成示例 |
| 离线演示（空数据目录、空向量缓存） | `matter-66e706190c`；8 findings（合并前口径）；分析 34670 ms（含首次索引）；确认/导出正常 | 设 `LEXFLOW_DATA_DIR/ARTIFACTS_DIR/DB/EMBED_CACHE` 到全新临时目录后跑 demo | mock + 冷启动 |
| 示例 DOCX 评测 | 条款 7/7、风险召回 **4/4**、findings **4**、溯源 4/4、五闸全过 4/4；`bm25-only` | `uv run python eval/run_eval.py` | mock + 自造样本 |
| 示例 PDF 评测 | 条款 7/7、风险召回 **4/5**、findings **5**、溯源 5/5、五闸全过 5/5；`bm25-only` | 同上 | mock + 自造样本 |
| HITL 第 1 次进程 | `pid 83656`、`matter-ffbe777ef2`、`awaiting_review`、findings 10、18858 ms、`has_interrupt=true` | `uv run python scripts/hitl_demo.py start` | mock |
| HITL 第 2 次进程 | `pid 84840`（新进程）、`completed`、findings 10、208 ms、docx **38740 B** | `uv run python scripts/hitl_demo.py resume` | mock |
| HITL 复验（跨路合并后） | `matter-16a11418a6`、`awaiting_review → completed`、findings **5**、`new_process=true`、docx **38549 B** | `scripts/hitl_demo.py start` → `resume`（2026-11-07 复核） | mock |
| 节点耗时 | parse 259 / segment 7 / review_commercial 647 / review_legal 634 / bind_evidence 349 / verify 0 / assemble_report 13 / export_docx 188 ms；合计 2097 ms | `uv run python -m lexflow.cli timeline matter-ffbe777ef2` | 真实计时 |
| CLI mock 端到端 | `matter-831b2b0fc7`、`completed`、findings 8、14388 ms、`hybrid/cuda`、节点合计 1415 ms | `uv run python -m lexflow.cli analyze examples/sample_service_contract.docx --engine mock --auto-approve` | mock |
| 合成集 v1（12 份 / 18 处） | 条款召回 **18/18**、precision **18/18**、findings 18、溯源 18/18、五闸全过 18/18 | `uv run python eval/run_synthetic_eval.py --gold data/synthetic_v1/gold.jsonl --out-csv eval/synthetic_results_v1_after.csv --out-md eval/synthetic_results_v1_after.md --label "v1-after"` | 合成数据 |
| 合成集 v2（18 份 / 30 处） | 条款召回 **30/30**、precision **30/30**、findings 30、溯源 30/30、五闸全过 30/30 | `uv run python eval/run_synthetic_eval.py` | 合成数据 |
| 种子缺陷（五闸双向） | 8 类植入错误 **281/281** 检出（20 份合同 / 39 条 findings）；对照组误报 **0/39** | `uv run python eval/seeded_defect.py` | mock + **程序植入**，非真实错案 |
| API 引擎（真实模型） | docx：召回 **3/4**、findings 8、溯源 8/8、五闸 1/8、¥0.1552；pdf：召回 **5/5**、findings 12、溯源 12/12、五闸 2/12、¥0.1217 | `uv run python eval/run_eval.py --engine api` | **真实 API 单次**，样本 2，详见 §4.6 |
| 真实劳动合同冒烟 | mock **1** 条 findings vs api **13** 条；api 溯源 **13/13**、引用覆盖 6/13、¥0.2045、163 s | `uv run python eval/run_contract_smoke.py --dir examples/real --engine api` | 真实合同 1 份，无 gold，详见 §4.7 |
| 法条语料 | 1465 条（1260 / 107 / 98）；两次构建 SHA256 相同；劳动法 107 条归一化、正文替换 1018 字、残留 0 | `uv run python scripts/build_corpus.py`（连续两次） | 真实法条 |
| 检索评测对齐 | 4 个文件 35/95/125/200；按 question 去重 330；剔除 5 条空标注；纳入 **325** 题；gold 条号 **626** | `uv run python eval/run_retrieval_eval.py` | 外部标注 |
| 检索三档（评测脚本路径 GPU） | dense/hybrid/rerank 条号召回@5 = **405/626、398/626、439/626**；@10 = **468/626、471/626、501/626**；问题命中@5 均 325/325 | 同上，见 `eval/retrieval_results.md` | 外部标注 |
| CPU 容器 `/health` | 首次 `indexing`、`vector_ready=false`；约 13s 后 `hybrid`、`vector_ready=true`、`embedding_device=cpu`；articles 1465 | `LEXFLOW_API_PORT=18000 docker compose up -d api` + `curl /health`（阶段 4 记录） | 容器实测 |
| CPU 容器 `/analyze` | `awaiting_review`、findings 8、`elapsed_ms=7498`、`hybrid/cpu`、verify 24/24 | 同上（阶段 4 记录） | 容器实测 |
| GPU 容器 | 构建成功；容器内 `torch.cuda.is_available()=True`（RTX 4060），但 bge-m3 加载 OOM → `embedding_device=null`、`retrieval_mode=bm25-only`；`/analyze` findings 8、`elapsed_ms=463`、`retrieval_device=n/a`；review/export 正常 | 见 [§5.3](#53-gpu-容器最终结论) | 容器实测，GPU 未启用 |


- mock 下 `llm_calls=14`、`prompt_tokens=0`、`completion_tokens=0`、`cost_estimate_cny=0.0`（未调用模型，token 与成本是真实的 0）。
- 阶段 2 的 CLI hybrid/cuda 实测为 19.6–20.1s（主要是 Windows CUDA 初始化与 bge-m3 加载）；最终回归的 CLI 14388 ms 同样包含模型加载，不是稳态查询耗时。
- GPU 相关行的口径：`检索三档（评测脚本路径 GPU）` 是 `eval/run_retrieval_eval.py` 直接调用 retriever/embedder 的脚本路径；demo/CLI 里的 `hybrid/cuda` 是当时那一次运行的状态。2026-11 top-k 实验从 service 链路调用 CUDA 编码时出现 `cudaErrorMemoryAllocation` 并降级 `bm25-only`，因此交付口径按 service 链路 CPU 运行（见 [§2.6](#26-检索与降级)、[§4.5](#45-top-k-对照实验与结论2026-11)）。

### 4.2 检索评测口径与耗时

- 只用能对齐到法条条号的标注：QA 集 `expected_articles` 为 `法名#条号`；LexWeave gold 为 `law_id#数字`，先转中文条号再校验存在性；一条问题的所有标注条号都能对齐才纳入，否则整条剔除并计数。
- 分档：`dense`（仅向量）、`hybrid`（BM25 + 向量 RRF）、`hybrid+rerank`（hybrid top20 → bge-reranker）。
- 指标：问题命中（top-k 至少命中 1 条 gold）与条号召回（top-k 覆盖的 gold 条号数 / gold 总数）；只输出原始计数与比率，**不设目标值、不写“效果良好”**。
- 4 个集合并非独立样本，`qa_test_cases(40) ⊂ qa_set_100 ⊂ qa_set_130`，按 question 去重后合并评测。

| 文件 | 行数 | 对齐纳入 | 剔除 | 剔除原因 |
|---|---|---|---|---|
| `LawAnswer\eval\qa_test_cases.jsonl` | 40 | 35 | 5 | `expected_articles` 为空 |
| `LexSynergy Legal QA System\eval\qa_set_100.jsonl` | 100 | 95 | 5 | 同上（同一批 5 条） |
| `LexSynergy Legal QA System\eval\qa_set_130.jsonl` | 130 | 125 | 5 | 同上（同一批 5 条） |
| `LexWeave\data\gold\gold_200.jsonl` | 200 | 200 | 0 | — |
| 合并去重后 | 330 | 325 | 5 | 空标注 |

| 档位 | 问题命中@5 | 条号召回@5 | 条号召回率@5 | 问题命中@10 | 条号召回@10 | 条号召回率@10 |
|---|---|---|---|---|---|---|
| dense | 325/325 | 405/626 | 0.6470 | 325/325 | 468/626 | 0.7476 |
| hybrid | 325/325 | 398/626 | 0.6358 | 325/325 | 471/626 | 0.7524 |
| hybrid+rerank | 325/325 | 439/626 | 0.7013 | 325/325 | 501/626 | 0.8003 |

- 共 626 个 gold 条号标注；单题 gold 条号数可为多个。「问题命中」全部 1.0000 说明这批问题在 top-5 内至少能命中一条标注，**不代表多标注题的全部条号都命中**，因此并列给出条号召回。
- **两设备召回数字完全一致**（405/626、398/626、439/626；468/626、471/626、501/626）；未观察到浮点或批次差异。
- 耗时（GPU）：索引缓存读取/模型加载 17.61s（含 CUDA 初始化，不是稳态查询耗时）、reranker 加载 5.37s、325 条查询编码 2.28s、dense 1.38s、hybrid 1.22s、hybrid+rerank 148.58s（约 **0.457s/题**）。
- CPU 对照（`torch 2.15.0+cpu`）：索引 13.35s、编码 11.39s、dense 1.99s、hybrid 1.73s、hybrid+rerank 1000.88s（约 **3.08s/题**）。
- 以上数字只描述这批离线标注在该语料、该设备上的检索结果，不能外推到真实合同审查质量。

### 4.3 可对外讲的实测事实（每条都有命令）

1. 无 API key、全程离线，一条命令跑完「上传 → 分析 → 风险表 → 确认 → docx → 指标」：`scripts/demo.py`，真实输出见 [docs/DEMO.md](docs/DEMO.md)。
2. 跨进程恢复：`hitl_demo.py start` 停在 `awaiting_review`，新进程 `resume` 从 SQLite checkpoint 完成导出（pid 83656 → 84840，docx 38740 B）。
3. 测试全绿：`uv run pytest -q` → **56 passed, 1 warning**（2026-11-04 复核；含 API 层 `/health` 状态流转、`/analyze`、`/review`、`/export`、跨 service 实例 resume）。
4. 法条语料 1465 条，构建幂等（两次 SHA256 相同）；劳动法康熙部首归一化 1018 字、残留 0。
5. 合成集 v1 18/18、v2 30/30（注入式 gold），并明确这是“注入风险是否被检出”，不是法律效果。
6. 检索评测 325 题 / 626 个 gold 条号，三档条号召回@5 405/626、398/626、439/626，且 CPU/GPU 召回一致。
7. 本机 GPU 可用性有分叉：`torch 2.14.1+cu130`、RTX 4060 Laptop 8187 MB；直接调用 embedder 的脚本路径走 `cuda`，service 链路本次 CUDA 编码报 `cudaErrorMemoryAllocation` 降级 `bm25-only`，交付按 CPU 运行 service（见 [§2.6](#26-检索与降级)、[§4.5](#45-top-k-对照实验与结论2026-11)）。
8. CPU Docker 容器可启动并通过 `/health` 与 `/analyze`（articles 1465、findings 8、`hybrid/cpu`）。

### 4.4 不可外推项（面试时必须单列）

- mock 规则表在示例/合成合同上的召回与 precision **不是真实合同审查准确率**；合成 gold 由注入规则反推，与规则表同构。
- 真实 API（DeepSeek/Qwen）的抽取质量与引用准确率**尚未评测**；本次 top-k A/B 只有 6 份样本上的成本/耗时与 5 条引用抽检，不能当作 API 效果结论（见 [§4.5](#45-top-k-对照实验与结论2026-11)）。
- 检索评测的 325 题来自外部既有 QA/gold 集，题目与法条词汇重合度高；问题命中 top-5 已饱和，不代表真实用户查询分布。
- 两路审查未合并，findings 计数包含法务/商务各一份；不能把条数直接当“风险点数量”。
- 没有真实合同样本、律师标注、扫描件合同、并发/多租户压测数据。
- 真实 API 的引用适用性未评测：top-k 抽检 5 条中 2 条明显错位（劳动法 79、民法典 628），三闸无法拦截（见 [§4.5](#45-top-k-对照实验与结论2026-11)）。
- docx 的段落定位锚是逻辑定位，不是纸张页码。

### 4.5 top-k 对照实验与结论（2026-11）

**实验设计（严格单变量）**：只改「每个条款检索候选法条数」这一个变量，A 组 `LEXFLOW_STATUTE_TOP_K=3`（默认），B 组 `LEXFLOW_STATUTE_TOP_K=10`。两组使用同一批 6 份合同、同一 `engine=api`、同一 `stance=甲方`、同一 CPU 检索设备（`LEXFLOW_EMBEDDING_DEVICE=cpu`）；开跑前先做预热检索校验，若模式不是 `hybrid` 直接中止，保证两组可比。

- 6 份样本：`examples/sample_service_contract.docx`、`examples/sample_procurement_contract.pdf`、`data/synthetic_v1/contracts/tech_0.docx` ~ `tech_3.docx`（后 4 份是 v1 gold 的前 4 条）。
- 来源：本机单次 A/B 运行记录（`AnalyzeService.analyze`，与 CLI 同一条链路），逐文件记录 findings、引用、三闸、token、成本与耗时。

> **口径提示**：本节 A/B 实验完成于五道闸与跨路合并上线**之前**，表中的 findings 数与「三闸全过」都是当时口径。当前链路为五闸 + `(clause_id, risk_type)` 合并，同一份合同的 findings 数约为当时的一半，**不可与本表直接对比**；本节结论（闸门通过率不是检测质量指标）仍然成立。

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

> **分母口径**：三闸「通过 / 分母」的分母是**该组全部 findings**（A=87、B=88），不是带法条条数，也不是条款数。未带法条的 finding 三闸必然不通过，所以 `17/87 → 48/88` 同时包含「没有引用」和「引用了但校验失败」两类。G1/G2/G3 的分母同样都是全部 findings。A 组 G1 通过数与带法条数相等（20/20）；B 组为 49/51，差的 2 条是证据账本中没有对应条号（见本节末）。

- v1 指标口径（4 份 `tech_*.docx`，注入 gold 共 6 个条号）：precision = 命中条号数 / 实际报出的不同条号数（A/B 均为 5/30）；recall = 命中条号数 / 注入 gold 条号数（A/B 均为 5/6）；溯源 = 有原文引用的 finding 数 / 该组合成合同的 findings 数（A 57/57、B 61/61，均为 100%）。**precision 与 recall 两组完全不变**，扩召回没有提升条款级检出。
- 两组数据库写入均一致：A 87/87、B 88/88（每个 matter 的 DB findings 数 = 报告 findings 数）。

**5 条新增引用抽检（B 组 41 条新增引用中人工挑 5 条，覆盖 4 个文件）**：

| # | 文件 / 条款 | 条款摘要 | 新增引用 | 适用性判断 |
|---|---|---|---|---|
| 1 | `sample_service_contract.docx` C0003 | 收到交付物后三日内验收，逾期未异议视为合格 | 民法典第六百二十二条 | **跨编类推**：622 属买卖合同编「检验期限过短」规则，用于服务合同验收只能类推，不是直接依据 |
| 2 | `sample_procurement_contract.pdf` C0005 | 逾期交货按日千分之三违约金，未设上限 | 民法典第五百八十五条 | **适用**：合同编违约责任通则，直接对应违约金约定与调减 |
| 3 | `tech_3.docx` C0007 | 保密期限为合同终止后两年 | 民法典第五百五十八条 | **适用**：合同编通则的后合同义务（通知、协助、保密、旧物回收），与保密期限问题对应 |
| 4 | `sample_service_contract.docx` C0006 | 争议由甲方所在地人民法院管辖 | 中华人民共和国劳动法第七十九条 | **明显错位**：服务合同管辖引劳动法劳动争议调解仲裁条文；模型理由还写了「若本合同属于劳动合同」，该前提在样本中不成立 |
| 5 | `tech_1.docx` C0002 | 按月对账、次月十五日前结算 | 民法典第六百二十八条 | **明显错位**：628 属买卖合同编价款支付时间，用于服务合同付款条款属跨合同类型误引 |

小计：2 条适用、1 条跨编类推、2 条明显错位（劳动法 79、民法典 628）。

**核心限制**：**闸门不校验适用性，top-k 扩召回会让「条号对但法条错」的绝对数量上升。** G1 只校验条号存在、法名与条号一致及证据账本；G2 只校验引文逐字一致；G3 只校验 as-of 时效。一条法条可以三闸全过却在法律上与条款无关。B 组三闸全过绝对数从 17 升到 48，同时带法条数从 20 升到 51，但抽检 5 条中就有 2 条明显错位，说明**闸门通过率不是检测质量指标**。（2026-11-07 新增 G4/G5 后该结论不变：G4 复判的是「条款是否构成该风险」，G5 校验的是数值出处，两者都不判断法条与条款的语义相关性。）

**结论：默认 `top_k` 保持 3**，10 只作为可选开关 `LEXFLOW_STATUTE_TOP_K=10`。理由：

1. **检测质量未提升**：v1 precision 5/30、recall 5/6 两组完全相同，findings 仅 87→88（+1）；扩召回只增加引用数量，不改变条款级检出。
2. **成本 +25.3%**：¥1.0270 → ¥1.2869，prompt token +65.8%。
3. **抽检适用性差**：5 条新增引用中 2 条明显错位、1 条跨编类推，而三闸无法拦截。

默认行为复核：不设置 `LEXFLOW_STATUTE_TOP_K` 时 `Settings().statute_top_k == 3`（2026-11-04 实测），与 A 组一致。

**成本合计**：有效 A+B = ¥2.3138（1.026964 + 1.286868）；另有一次因向量层降级 `bm25-only` 而作废的 B 组空跑 ¥1.3106；累计投入 ¥3.6244。作废那次的 6 个 matter 仍在 DB 中，已从本表与结论中排除。

- 其他已知偏差：B 组有 2 条带法条 finding 的 G1 不通过（`sample_service_contract.docx` C0002 引民法典第八百八十二条、`tech_2.docx` C0007 引民法典第五百五十八条，均为「证据账本中不存在」），说明扩召回也不能保证证据账本覆盖。

### 4.6 mock 与真实 API 对照（2026-11-07）

命令：`uv run python eval/run_eval.py --engine api`（结果落 `eval/results_api.csv`、`eval/results_api.md`）。

| 指标 | mock（docx / pdf） | API（docx / pdf） |
|---|---|---|
| 条款数 | 7/7 / 7/7 | 7/7 / 7/7 |
| 风险条款召回 | 4/4 / 4/5 | **3/4 / 5/5** |
| findings | 4 / 5 | 8 / 12 |
| 溯源率 | 4/4 / 5/5 | **8/8 / 12/12** |
| 五闸全过 | 4/4 / 5/5 | **1/8 / 2/12** |
| LLM 调用 | 14（计次不计费） | 14 |
| tokens(in/out) | 0 / 0 | 9,720/16,976；9,278/12,890 |
| 成本 | 0 | **¥0.1552 / ¥0.1217** |
| 耗时 | 约 0.3 s | 68.6 s / 69.0 s |

**三个必须一起说的结论**：

1. **召回打平，但漏的和补的位置不同。** 合计 8/9，与 mock 相同。差别在于：docx 的 C0006（管辖）被真实模型**整条不报**，而 pdf 的 C0006 反而补上了。原因写在 warnings 里——模型判定「候选法条不覆盖协议管辖效力，民事诉讼法相关规定未在候选法条中」，选择不产出 finding；mock 规则则用民法典第四百七十条勉强挂上。**同一份语料，规则引擎会硬报，模型会不报，两种错法不同。**

2. **溯源率 100%（8/8、12/12）。** 这是真实模型下最稳的一项：每条 finding 都能回到条款原文的段落号与字符 span，与有没有引用法条无关。

3. **五闸全过率只有 12.5%–16.7%，不是闸门坏了。** 20 条 finding 里 17 条 `law_name` 为空——模型主动拒引（判定候选法条不覆盖该风险）。按现有口径（G1 要求必须有真实条号），无引用必然不过。**这暴露的是产品口径问题，不是校验问题**：无引用的风险提示不该和「有引用且校验通过」混在同一个通过率里。

> 如果要改，方向是给 finding 加 `citation_status`（`cited` / `uncited_by_model`），两类分开统计；本次不改，避免与既有 87/88 口径的 A/B 实验混在一起。

**局限**：样本 2 份、单次运行、`bm25-only`（关闭向量以规避容器内存问题）；单份成本 ¥0.12–0.16，30 份合成集全跑约 ¥4、约 35 分钟，未做。

### 4.7 真实劳动合同冒烟（2026-11-07）

样本：`examples/real/labor_contract.docx`（795 字 / 6 条款），来自本地一份公开劳动合同模板，含多处明显违法条款。**这是仓库里唯一的非合成、非示例合同**，用于验证「管线在没见过的文档上会不会退化」——这类指标不需要 gold。

命令：`uv run python eval/run_contract_smoke.py --dir examples/real --engine {mock|api}`

| 引擎 | findings | 溯源率（严格） | 引用覆盖 | 五闸全过 | 成本 | 耗时 |
|---|---:|---:|---:|---:|---:|---:|
| mock | **1** | 1/1 | 1/1 | 1/1 | 0 | 277 ms |
| api | **13** | **13/13** | 6/13 | 5/13 | ¥0.2045 | 163 s |

**结论一：规则表是领域绑定的。** 9 条 mock 规则是按商务合同写的（付款、违约金上限、解除、管辖、保密、不可抗力、责任上限、验收、交付），遇到劳动合同只命中 1 条。真实模型在同一份文档上报出 13 条，覆盖：

- 试用期工资 70% 低于法定 80% 下限（劳动合同法第二十条）
- 试用期期限未与合同期限挂钩（第十九条）
- 岗位/工作地点可单方随时调整、乙方「必须服从」（第八条）
- 工资由甲方单方确定、可用实物替代（第八十五条）
- 社保办理责任全部转嫁劳动者（劳动法第一百条）
- 工伤责任由乙方自行承担（劳动法第九十一条）
- 不得结婚或生育、否则单方解除不给补偿（**未引法条**）
- 提前离职违约金为上月工资 3 倍（未引法条）
- 入职收取押金（未引法条）
- 未尽事宜由甲方单方解释（未引法条）

**13 条全部可溯源到条款原文**（引文是条款正文子串），6 条引到具体条号。这说明：规则引擎的上限由规则表决定，模型的上限由 prompt 与检索决定；换合同类型时前者直接失效，后者仍能工作。

**结论二：合并键不够用。** C0005（工伤责任）与 C0006（单方解释权）各被报了两次——两路给出的 `risk_type` 不同，`(clause_id, risk_type)` 合不上。这是 §3.2 合并口径的已知边界，不是本次改动引入的。

**局限**：真实合同只有 1 份，不足以支撑任何比率结论；上表只说明「能跑通且抓对了这些点」，不是准确率。

---

## 5. Docker 与 GPU

### 5.1 镜像里放什么 / 不放什么

- 放入镜像：Python 3.12.14、`uv` 安装的全部依赖（含 `sentence-transformers`、`faiss-cpu`）、`lexflow/` 代码、`assets/templates/risk_report.docx`。
- 不放入镜像：**模型权重**（bge-m3 / bge-reranker，约 2.3GB/个）、`data/`（语料、向量缓存、SQLite）、`artifacts/`（导出 docx）、`eval/`、`tests/`、`examples/`。
- 因此镜像可重建，模型与数据通过 bind mount 持久化，容器删除后不丢。

### 5.2 启动与挂载

| 宿主机 | 容器内 | 说明 |
|---|---|---|
| `./data` | `/app/data` | 语料、向量缓存、`lexflow.db` |
| `./artifacts` | `/app/artifacts` | 导出 docx |
| `${LEXFLOW_MODEL_HOST_DIR:-../模型}` | `/models`（只读） | bge-m3 与 reranker |
| — | `LEXFLOW_MODEL_DIR=/models` | 容器内模型路径 |
| — | `LEXFLOW_DATA_DIR=/app/data`、`LEXFLOW_DB=/app/data/lexflow.db` | 数据路径 |

```powershell
# 首次：构建并启动 API + Streamlit（默认装 CPU 版 torch）
docker compose up --build -d

# 查看状态与容器内 health（本机 8000 落在 Windows 排除端口段时用 18000）
docker compose ps
curl http://127.0.0.1:18000/health

# 离线启动（镜像已构建、模型已在宿主机；不重新 build）
docker compose up -d

# 校验配置（不启动容器）
docker compose config --quiet
```

- 默认镜像是 CPU torch，`docker compose up` 不会因为无 GPU 失败；GPU 覆盖层只给 `api` 加 `gpus: all`，并把 `LEXFLOW_DEVICE` 设为 `cuda`。
- 本机实测 Docker Hub registry 不可达（`docker manifest inspect python:3.11-slim` 超时），因此 `Dockerfile` 使用本机已有基础镜像 `python:3.12.14-slim-bookworm`。
- 已通过 `docker version` / `docker info` 确认 engine 可用：Server 29.8.0、context `desktop-linux`、driver overlayfs；Docker 有 `nvidia` runtime。
- CPU 容器冒烟（阶段 4 重建镜像时镜像 `lexflow:0.1.0` 为 5.13GB；阶段 5 GPU 构建后同一 tag 为 4.23GB）；
  - 首次 `/health`：`indexing`、`vector_ready=false`、`embedding_device=null`；
  - 约 13s 后：`hybrid`、`vector_ready=true`、`vector_backend=cache`、`embedding_device=cpu`、articles 1465；
  - 容器内 `/analyze`（mock，docx）：`awaiting_review`、findings 8、`elapsed_ms=7498`、`metrics.retrieval_mode=hybrid`、`retrieval_device=cpu`，verify 24/24；
  - 冒烟结束后 `docker compose down`。
- 宿主机模型目录可用 `LEXFLOW_MODEL_HOST_DIR` 覆盖；首次容器内建索引会在 `./data/embeddings` 生成缓存，重跑读缓存。

### 5.3 GPU 容器最终结论

> 本节结论以本机最终一次构建/运行记录为准；失败也给出可复现的错误原文。

GPU 覆盖层配置：`docker-compose.gpu.yml` 设置 `gpus: all`、`LEXFLOW_DEVICE=cuda`，并把构建参数 `TORCH_INDEX` 指到 `https://download.pytorch.org/whl/cu130`。

**原始失败（在线安装 cu130 torch）**：

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml build --progress=plain
```

`uv pip install --index-url https://download.pytorch.org/whl/cu130 torch` 在阶段 3 记录为 exit code 2，但当时未保留完整输出。本阶段重新执行同一构建时，该步骤长时间停在下载且没有报错；隔离复现（全新 `python:3.12.14-slim-bookworm`、限时重试）在 30 分钟内持续下载了 >5.9 GB 的 torch + CUDA 依赖，未以 exit 2 结束。可复现的失败原文只有容器到 PyPI 的连接超时：

```text
WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broken by
'ConnectTimeoutError(...Connection to pypi.org timed out. (connect timeout=15)')': /simple/uv/
```

结论：wheel 本身存在（宿主机 `https://download.pytorch.org/whl/cu130/torch/` 可访问，cp312 manylinux x86_64 有 `2.14.1+cu130`）；问题是**容器内到外网 index 慢且不稳定**，在线安装 500MB+ 的 CUDA wheel 及其 2GB 级依赖不可控。exit code 2 的完整错误无法复现，因此不把它当作唯一结论，改用可复现的本地 wheel 方案。

**方案 1（采用）：宿主机预下载 wheel + 构建时 COPY 本地安装**

```powershell
# 宿主机下载（Linux cp312 cu130 wheel）
curl.exe -L -o torch-2.14.1+cu130-cp312-cp312-manylinux_2_28_x86_64.whl `
  "https://download.pytorch.org/whl/cu130/torch-2.14.1%2Bcu130-cp312-cp312-manylinux_2_28_x86_64.whl"
# 放到 docker/wheels/（该目录 *.whl 已在 .gitignore 中排除）
```

`Dockerfile` 会先 `COPY docker/wheels /tmp/torch-wheels`，若存在 `*.whl` 则
`uv pip install /tmp/torch-wheels/*.whl`，否则回退到 `--index-url ${TORCH_INDEX} torch`。
这样不再下载 500MB+ 的 torch wheel；但仍需从 PyPI 拉取 CUDA 依赖（约 2GB），构建耗时约 20 分钟（本机实测）。

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml build --progress=plain
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

**实测结果（方案 1：本地 wheel）**：

- 构建成功：`lexflow:0.1.0`（本地 wheel 安装 torch，容器内不再下载 torch）；启动后 `docker exec ... python -c "import torch"` 输出
  `2.14.1+cu130 True NVIDIA GeForce RTX 4060 Laptop GPU`。
- `/health`：首次 `indexing`、`vector_ready=false`；模型加载失败后（约 10–40s 内）落到 `bm25-only`，`embedding_device=null`，
  `vector_reason=embedding 模型加载失败（bge-m3）: OutOfMemoryError: CUDA out of memory. Tried to allocate 20.00 MiB. GPU 0 has a total capacity of 8.00 GiB of which 5.46 GiB is free...`
- `/analyze`（docx，mock）：`matter-a621a54f30`、`awaiting_review`、findings 8、`elapsed_ms=463`、
  `retrieval_mode=bm25-only`、`retrieval_device=n/a`；节点耗时 parse 130 / segment 19 / review 61+55 / bind_evidence 42 / assemble 115 ms。
- `/review` → `completed`；`/export` → HTTP 200、docx 38563 B。

**OOM 的根因（已隔离验证）**：容器内可正常做小分配，但单次 `cudaMalloc` 在约 640–768 MB 之间失败，即使
`torch.cuda.mem_get_info()` 报告 6.9 GB free：

```text
free/total (7440695296, 8585216000)
256 MB ok
512 MB ok
640 MB ok
768 MB FAIL CUDA out of memory. Tried to allocate 768.00 MiB. GPU 0 ... 6.92 GiB is free
```

这是 Docker Desktop on Windows（WDDM）给容器的分配预算限制，不是模型文件缺失或 wheel 不匹配：
bge-m3 的 fp32 权重需要 >1 GB 的单次分配，因此无法在容器内加载。`expandable_segments:True` 也不能绕过。

**最终结论**：在本机 Docker Desktop 形态下，**无法在容器内启用 GPU 检索**。容器内 torch 的 CUDA 可见性正常，
但应用层 embedding 会降级为 `bm25-only`，`embedding_device=null`、`retrieval_device=n/a`。
交付形态采用**宿主机脚本路径 GPU + CPU 服务/容器**：宿主机只有直接调用 `LocalEmbedder` 的评测脚本路径实测 `cuda`；
service 链路（含容器）按 CPU 运行，容器负责 API/UI 编排与检索（容器 health、analyze、review、export 均已实测）。
若未来在 Linux 主机或 TCC 模式下有正常的显存预算，可继续沿用 `docker-compose.gpu.yml` 与本地 wheel 构建路径，
但不能把本机容器称为“已启用 GPU”，也不能把宿主机 service 链路称为“GPU 可用”。


**方案 2（未采用）：改用 `nvidia/cuda` 基础镜像**。本机无该镜像缓存，且 Docker Hub 不可达；即使改用 CUDA 基础镜像，torch 仍需要下载 cu130 wheel，无法绕开容器网络问题，因此不作为首选。

**交付形态（若无 GPU）**：宿主机只在直接调用 embedder 的脚本路径用 `cuda`（本机已验证），service 链路与容器按 CPU 运行；两种形态共用同一套数据与挂载。CPU 容器的 `/health`、`/analyze` 已实测通过。

---

## 6. 已知限制

- mock 是 9 条显式规则的确定性匹配，覆盖条款类型有限；未命中时返回空结果，不输出「未发现风险」之类的结论。
- 甲方立场的管辖/验收规则沿用阶段 3 的「单方条款均报」语义（包括有利于甲方自身的那一侧），只有乙方立场才做方向过滤（只报甲方一侧）。这是为保持既有 synthetic gold 与 18/18、30/30 不变的有意选择，不是对等的双向评测；乙方立场目前只有最小用例，没有独立 gold 集。
- 「预付款」子串误报已修复（同一 v1 数据集 precision 18/22 → 18/18）；但规则仍是人工编写，对未覆盖的表述类型可能漏检或误报。
- 合成合同 v2 为 3 个模板 × 6 个变体 = 18 份、8 类风险；分布与真实合同差距大；注入式 gold 只覆盖注入风险，不覆盖模板中未注入的其他不利条款。v2 的 30/30 是规则与注入刻意对齐的结果。
- 管辖规则只引用民法典第四百七十条（争议解决方法）；本地语料不含民事诉讼法，规则不能判断管辖条款的实体效力，只能提示核对。
- 解除规则只把无理由/随时/单方解除视为风险；「两次维修仍不能正常使用后可解除」这类合法救济条件不命中，因此在示例采购 PDF 的 5 条自造 gold 中漏 1 条（`C0006`）。
- 跨路合并按 `(clause_id, risk_type)` 做，`risk_type` 为空或 `other` 时不合并——没有可靠类型键时留两条。示例 DOCX 现为 4 条 findings 对应 4 个风险点。
- 混合检索的向量部分已接入本地 bge-m3。**GPU 可用性有分叉**：直接调用 `LocalEmbedder` 的脚本路径可用（`torch 2.14.1+cu130` / RTX 4060 8187 MB）；service 链路（`AnalyzeService` → 图节点 → 检索）调用 CUDA 编码时报 `cudaErrorMemoryAllocation`，向量层降级 `bm25-only`。交付口径是 service 链路按 CPU 运行。
- 检索评测的 325 题来自外部既有 QA/gold 集，题目与法条存在较强词汇重合，问题命中在 top-5 已饱和；条号召回仍只覆盖 626 个标注的一部分，不能外推。
- bge-reranker 在 GPU 上约 0.46s/题（20 候选），CPU 约 3.08s/题；默认不开启。
- 失败归档 `data/failures.jsonl` 只追加、不做轮转；真实 API 下的非法输出比例尚未统计。
- `/health` 并发竞态已修复，但首次建索引仍可能需要较长时间（CPU 全量编码约 2 分钟，缓存命中则秒级）。
- docx 文件格式不保存分页信息，本系统**不做页码推断**：docx 的 `page_no` 置空，改用 `§段落号 [章节标题] [chars span]` 定位锚；PDF 的 `page_no` 来自 PyMuPDF 真实版面块，但它是 **PDF 页序（page index + 1），不是打印页码**，有封面/页码偏移时不能画等号。
- Streamlit UI 只做了代码层与 API 层的验证，**未做浏览器端全链路点击验证**（上传→分析→确认→下载没有用浏览器自动化跑过）；演示与回归以 `scripts/demo.py`、CLI 和 API 测试为准。
- 示例采购 PDF 的风险召回为 4/5，漏掉的是 `C0006`（“两次维修仍不能正常使用后可解除”）；规则只把无理由/随时/单方解除视为风险，未覆盖该合法救济条件。
- `eval` 的示例合同召回率基于自造标注，样本量 2，不能外推；合成集指标也不能外推。
- 扫描件 OCR 未做自动化测试；未安装 `ocr` extra 时会明确报错。
- 未做并发/多用户隔离，API 为单机演示用途。
- GPU 容器的最终可用性见 §5.3；即使容器内 `cuda` 不可用，也只影响容器，不影响宿主机直接调用 embedder 的脚本路径。
- **闸门不校验「法条适用性」**：G1 查条号存在、G2 查引文逐字、G3 查 as-of 时效、G5 查数值有原文出处，G4 用规则的判定项/排除项复判「这一条款是否真的构成该风险」。**这五道没有一道判断「这条法条是否适用于这个条款」**，所以「条号对但法条错」的引用仍可通过。G4 挡的是条款级误报（如把写了上限的违约金报成无上限），不是法条级错配。top-k 扩召回会提高后者的绝对数量（见 [§4.5](#45-top-k-对照实验与结论2026-11)）。
- **规则表只覆盖商务合同**：9 条 mock 规则按付款 / 违约金 / 解除 / 管辖 / 保密 / 不可抗力 / 责任上限 / 验收 / 交付设计，遇到劳动合同等其他类型只命中个别条款（§4.7 实测 mock 1 条 vs api 13 条）。换领域要重写规则表，这不是调参能解决的；真实模型路径不受此限，但会引入拒引与不稳定。
- **真实合同样本只有 1 份**：`examples/real/labor_contract.docx`，不足以支撑任何比率结论，只能证明「管线在没见过的文档上跑通且抓对了这些点」。
- **种子缺陷 281/281 不能当「准确率」**：错误是程序植入的（改条号、改法名、改一个字、挪 as_of、换成无关条款、换成有上限条款、塞一个原文没有的数字、清空引用），每一类都精准对应一道闸，所以检出率接近必然。它证明的是「这五道闸的判据写对了」，不证明「能拦住真实律师会犯的错」。
- **误报率 0/39 是自洽检查**：对照组用的是系统自身产出的 findings（本来就该全过），不是独立人工标注的正确样本。真实误报率需要另一批人工核对过的正确引用，本仓库没有。
- **gold 只标注入风险**：v1/v2 gold 由注入规则反推，只覆盖被注入的风险条款，模板中未注入的其他不利条款不在 gold 内，也不是律师标注；指标不能外推。
- **precision 口径偏差**：v1 precision = 命中条数 / 实际报出的不同法条条数，模型漏报的条款不进入分母；该数值是链路一致性指标，不是法律准确率。
- **按条款类型预过滤未做**：本次只对照候选法条数量（top-k 3 vs 10），没有按条款类型先过滤候选法条；条款类型预过滤属于未验证的后续方案。

---

## 7. 未实现项（M1/M2 范围，代码中最多留占位）

- 类案检索与相似案例推荐
- 法条/条款知识图谱
- 时间线与履约节点抽取
- 完整金额测算（违约金/损失计算）
- 多轮对话式追问、批量审查、权限体系
- 真实 API 效果评测（当前无任何实测准确率/时延数据）
- 跨路审查结果合并（法务/商务同条款去重与冲突裁决）
- GPU 容器之外的分布式推理/多卡调度

### 示例与合成数据性质

`examples/` 下两份合同均为**自造样例**（由 `scripts/make_samples.py` 生成），仅用于链路验证，不是公开示范文本，也不构成律师意见。`eval/gold.jsonl` 同样为自造标注。

- 合成数据生成（默认 v2）：`uv run python scripts/gen_synthetic_contracts.py`
- v2 规模：3 个模板（技术服务/设备采购/软件开发）× 6 个变体 = **18 份合成 docx / 每份 12 条**
- v2 注入：**8 类风险**（付款/违约金/单方解除/管辖/保密/不可抗力/责任限额/验收标准），共 **30 处**
- v1 原始数据：`uv run python scripts/gen_synthetic_contracts.py --profile v1 --out data/synthetic_v1`（3×4=12 份、8 条、4 类风险、18 处注入；用于修改前后对照）
- gold：由注入规则反推（被注入风险的那一条即 gold），**不是律师标注**
- 复现：固定种子 `20261103`；v1 重新生成后 12 份文本 SHA 与阶段 1 落盘数据完全一致
- 性质：**合成数据，非真实合同分布；指标不可外推**
