# 示例合同评测（engine=api）（原始计数与比率）

> 引擎为 api；以下数字只描述本次运行。API 下为真实模型单次运行，样本量 2，不可外推。

| 样本 | 条款数 | 风险条款召回 | 风险项 | 严格溯源率 | 引用/未引用 | 五闸全过（有引用） | 双路：风险点/两路共同/法务独有/商务独有 | 调用 | tokens(in/out) | 成本CNY | 耗时ms | docx行数 | DB条数 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sample_service_contract.docx | 7/7 | 3/4 (0.75) | 8 | 8/8 (1.0) | 4/4 | 0/8 (0 条有引用) | 7/6/1/0 | 14 | 9728/18262 | 0.165552 | 99317 | 8 | 8 |
| sample_procurement_contract.pdf | 7/7 | 5/5 (1.0) | 11 | 11/11 (1.0) | 1/10 | 0/11 (0 条有引用) | 10/8/0/2 | 14 | 10116/17070 | 0.156792 | 73243 | 11 | 11 |

> 严格溯源 = `original_quote` 归一化后是所属条款正文的子串；五闸全过要求五道闸都是 pass，skipped 不算通过。

## 分闸门计数
- sample_service_contract.docx: G1 4/8；G2 4/8；G3 4/8；G4 0/8；G5 7/8（G5 skipped 1；含 skipped 的 finding 4 条）
- sample_procurement_contract.pdf: G1 1/11；G2 1/11；G3 1/11；G4 0/11；G5 9/11（G5 skipped 1；含 skipped 的 finding 2 条）
