# 示例合同评测（engine=mock）（原始计数与比率）

> 引擎为 mock；以下数字只描述本次运行。mock 下仅代表链路跑通。

| 样本 | 条款数 | 风险条款召回 | 风险项 | 严格溯源率 | 引用/未引用 | 五闸全过（有引用） | 双路：风险点/两路共同/法务独有/商务独有 | 调用 | tokens(in/out) | 成本CNY | 耗时ms | docx行数 | DB条数 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sample_service_contract.docx | 7/7 | 4/4 (1.0) | 4 | 4/4 (1.0) | 4/0 | 4/4 (4 条有引用) | 4/4/0/0 | 14 | 0/0 | 0.0 | 68413 | 4 | 4 |
| sample_procurement_contract.pdf | 7/7 | 4/5 (0.8) | 5 | 5/5 (1.0) | 5/0 | 5/5 (5 条有引用) | 5/5/0/0 | 14 | 0/0 | 0.0 | 2226 | 5 | 5 |

> 严格溯源 = `original_quote` 归一化后是所属条款正文的子串；五闸全过要求五道闸都是 pass，skipped 不算通过。

## 分闸门计数
- sample_service_contract.docx: G1 4/4；G2 4/4；G3 4/4；G4 4/4；G5 4/4（G5 skipped 0；含 skipped 的 finding 0 条）
- sample_procurement_contract.pdf: G1 5/5；G2 5/5；G3 5/5；G4 5/5；G5 5/5（G5 skipped 0；含 skipped 的 finding 0 条）
