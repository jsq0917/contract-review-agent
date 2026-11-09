# 3–5 分钟演示脚本（应用岗）

> 目标：当面演示「合同上传 → mock 审查 → 风险清单 → 人工确认 → 导出 docx → 指标」全链路。
> 全程离线、不需要 API key；所有产物标注 `engine=mock`，不是真实模型效果。
>
> 所有命令在项目根目录 `e:\project\lexflow` 执行。

## 0. 开场前 30 秒（准备）

```powershell
uv sync
uv run python scripts/demo.py
```

- 首次运行会加载本地 bge-m3 并读取向量缓存，本机冷启动约 15–35 秒（空向量缓存时需先建索引）。
- 如果只想看链路、不想加载模型：`$env:LEXFLOW_EMBEDDING_ENABLED="false"`，检索退化为 BM25-only，命令不变。
- 不需要 `LEXFLOW_API_KEY`；demo 强制 `engine=mock`。

**你可以说：**

> 这是一个合同审查的多智能体原型。没有模型 key 也能完整跑通，今天演示的是链路、结构化和可追溯性，不是法律效果。

---

## 1. 一条命令跑完（约 2 分钟）

```powershell
uv run python scripts/demo.py
```

命令内部固定 6 步，照着屏幕讲即可：

| 屏幕步骤 | 你讲什么 | 实际发生 |
|---|---|---|
| `[1/6] 上传示例合同` | 输入是仓库自带的示例合同，打印文件大小和 SHA256，方便核对样本没被换过 | 文件被复制到 `artifacts/<matter_id>/` |
| `[2/6] mock 分析` | 解析 → 切分条款 → 法务/商务两路审查 → 检索法条绑定证据 → 三道核验 → 组装报告；此时停在人工确认点 | LangGraph `interrupt()`，状态 `awaiting_review` |
| `[3/6] 风险清单` | 每条含风险等级、规则 ID、条款号、法条依据、定位锚、问题与建议，按 high > medium > low 排序 | 数据来自报告 `findings`，不是前端伪造 |
| `[4/6] 人工确认` | 模拟人点“确认”，`Command(resume=...)` 继续跑 | 审批记录写入 SQLite，状态变 `completed` |
| `[5/6] 导出 docx` | 打印 docx 路径与字节数 | docxtpl 渲染，风险表行数 = DB findings 数 |
| `[6/6] 指标` | 真实计数：耗时、LLM 调用次数、token、节点耗时、检索模式/设备 | 全部来自本次运行的 metrics |

**实测输出（本机一次运行，节选）：**

```text
[1/6] 上传示例合同
文件: E:\project\lexflow\examples\sample_service_contract.docx
大小: 37255 B   SHA256: a0745c7f4893109f6834d4e29d6fe9c5f3fe6477b9c7bf419b3f628d120b8e23

[2/6] mock 分析（离线，无 API key）
matter_id: matter-9b25b21a2b
engine: mock    status: awaiting_review    端到端耗时: 17835 ms
doc_id: a0745c7f4893109f

[3/6] 风险清单（8 条，按 high > medium > low 排序）
说明：以上为 2026-11-03 **合并前**的输出，法务/商务两路当场各报一次。2026-11-07 起合并下沉到 `merge_findings` reducer，同一 `(clause_id, risk_type)` 只留一条，同一份合同现在输出 **4 条**（见 README §3.2）。
#1  HIGH 规则 R-PEN-001  条款 C0004（商务）
    法条  中华人民共和国民法典 第五百八十五条
    定位  a0745c7f4893109f · §10 [chars 184:230]
#3  HIGH 规则 R-TERM-001 条款 C0005（商务）
    法条  中华人民共和国民法典 第五百六十三条
    定位  a0745c7f4893109f · §12 [chars 231:272]
#5  MED  规则 R-PAY-001  条款 C0002（商务）
    法条  中华人民共和国民法典 第五百二十六条
    定位  a0745c7f4893109f · §6 [chars 81:143]

[4/6] 人工确认（interrupt 后 resume）
status: completed    findings: 8

[5/6] 导出 docx
路径: E:\project\lexflow\artifacts\matter-9b25b21a2b\report.docx
大小: 38564 B

[6/6] 本次实测指标（真实计数，非估算）
总耗时（分析 + 确认/导出）: 17905 ms（分析 17835 ms，含检索冷启动；确认/导出 70 ms）
LLM 调用: 14（mock 每次审查计一次，token 恒 0）
检索模式: hybrid    设备: cuda
节点合计: 1677 ms
```

> 数字随机器和冷/热启动变化；脚本每次都打印真实值，不写死。
>
> 以上是 2026-11-03 的运行记录。2026-11 top-k 实验发现 service 链路调用 CUDA 编码会
> `cudaErrorMemoryAllocation` 降级 `bm25-only`，因此后续交付按 CPU 运行 service，GPU 仅保证
> 直接调用 embedder 的脚本路径（见 README §2.6 / §4.5）。

---

## 2. 可选加分项（每项 30–60 秒）

### 2.1 跨进程恢复（HITL 不是假的）

```powershell
uv run python scripts/hitl_demo.py start
uv run python scripts/hitl_demo.py resume
```

第一次进程停在 `awaiting_review`；第二次是**全新进程 + 全新 AnalyzeService**，从 SQLite checkpoint 恢复并导出。脚本两次都输出 JSON。

**你可以说：**

> 确认点不是前端按钮，而是 LangGraph 的 interrupt；进程杀掉后还能按 matter_id 接着跑。

### 2.2 节点耗时与日志

```powershell
uv run python -m lexflow.cli timeline <matter_id>
```

输出 `node_latency_ms`、`total_node_ms`、`llm_calls`、tokens、`retrieval_mode`、`retrieval_device`。
需要看原始 JSON 日志时给 demo 加 `--show-logs`，每行都带同一个 `matter_id`，可直接串起来。

### 2.3 API / UI（可选）

```powershell
uv run lexflow-api
uv run streamlit run lexflow/ui/app.py
```

UI 单页：上传 → 选立场 → 审查 → 风险表 → 确认/驳回 → 下载 docx；`engine=mock` 时顶部有红色横幅。
注意本机 8000 端口被其他服务占用时，换端口（如 `uv run uvicorn lexflow.api.main:app --port 18000`，并设 `LEXFLOW_API_URL`）。

---

## 3. 可能被追问的点（照着答，不越界）

| 追问 | 回答 |
|---|---|
| 准确率/召回率是多少？ | **没有真实合同上的准确率**。仓库里只有 12 份合成合同 + 注入式 gold，数字是「注入风险是否被规则检出」，见 `eval/synthetic_results*.md`；不能外推成法律效果。 |
| 示例合同 4/5、4/4 是什么意思？ | 是示例样本上的风险条款召回（4/5 = 原有 1 条 + 阶段 3 补的 3 条 detector），不是模型能力，见 README「实测数字与来源」。 |
| 为什么同一条款报两次？ | 已经不报了。合并放在 `merge_findings` reducer 里，按 `(clause_id, risk_type)` 合成一条，`reviewers` 记两路来源。之前是合并在节点里做、被 reducer 的并集语义撤销了——节点返回 4 条，reducer 把没覆盖到的 4 条又 union 回来。真实 API 下两路 prompt 不同，合并键相同，逻辑不变但效果仍未评测。 |
| docx 为什么没有页码？ | docx 本身不保存分页信息，不伪造页码；用「§段落号 + 章节标题 + 字符 span」定位。PDF 才用 PyMuPDF 的真实页码/bbox。 |
| token 为什么是 0？ | mock 不调用模型，token 恒 0，成本 0；真实 API 才记录原始 token，且未评测效果。 |
| 为什么 14 次 LLM 调用但 token 是 0？ | 调用次数是审查器被调用的次数（mock 也计次），token 只在真实 API 下产生。 |
| 容器里 GPU 能用吗？ | 见 README「Docker 与 GPU」的最终结论；本机只有**直接调用 embedder 的脚本路径**实测可用 GPU，service 链路按 CPU 运行，不能笼统说「本机 GPU 可用」。 |
| 能处理多大规模？ | 法条语料 1465 条；合同是单文件串行处理，没有并发/多租户压测数据。 |
| 真实 API 效果如何？ | 未评测。代码路径存在；top-k A/B 只在 6 份样本上记录了成本/耗时并抽检 5 条引用，不构成质量评测（见 README §4.5），演示不做效果宣称。 |

---

## 4. 不要说的话

- 不说「准确率 80%/召回 4/5」这类把合成集数字当真实能力的表述。
- 不说「GPU 容器已可用」，除非当场跑出 `/health` 的 `embedding_device=cuda`。
- 不说「支持并发/多租户」。
- 不说「这是法律意见」；报告和 UI 都写明仅链路演示。
