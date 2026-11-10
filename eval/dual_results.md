# 单路 / 双路对照结果

- 生成时间：2026-11-10T16:54:34.361656+00:00
- 引擎：`mock`
- 控制变量：同一批合同、同一语料、同一检索配置；只切换 `channels`。
- 风险点口径：`(clause_id, 归一化引文)`；`both_matches_union` 表示双路去重结果与两路并集一致。

| case | 法务单路 | 商务单路 | 双路 | 两路共有 | 法务独有 | 商务独有 | 并集 | 简单相加 | 双路=并集 |
|---|---|---|---|---|---|---|---|---|---|
| dev-synthetic-service | 4 | 4 | 4 | 4 | 0 | 0 | 4 | 8 | 是 |
| dev-synthetic-procurement | 5 | 5 | 5 | 5 | 0 | 0 | 5 | 10 | 是 |
| test-public-labor-template | 1 | 1 | 1 | 1 | 0 | 0 | 1 | 2 | 是 |

## 成本与延迟

| case | 模式 | 风险点 | findings | 耗时(ms) | tokens | 成本(CNY) | trace_id |
|---|---|---|---|---|---|---|---|
| dev-synthetic-service | legal_only | 4 | 4 | 20707 | 0 | 0.0 | `trace-matter-46b7566e94` |
| dev-synthetic-service | commercial_only | 4 | 4 | 959 | 0 | 0.0 | `trace-matter-93807260eb` |
| dev-synthetic-service | both | 4 | 4 | 1514 | 0 | 0.0 | `trace-matter-4e1fa61a73` |
| dev-synthetic-procurement | legal_only | 5 | 5 | 1312 | 0 | 0.0 | `trace-matter-9f51f016bb` |
| dev-synthetic-procurement | commercial_only | 5 | 5 | 1087 | 0 | 0.0 | `trace-matter-999f35435d` |
| dev-synthetic-procurement | both | 5 | 5 | 1572 | 0 | 0.0 | `trace-matter-a58bc4cd67` |
| test-public-labor-template | legal_only | 1 | 1 | 854 | 0 | 0.0 | `trace-matter-493386a5ae` |
| test-public-labor-template | commercial_only | 1 | 1 | 781 | 0 | 0.0 | `trace-matter-015e655508` |
| test-public-labor-template | both | 1 | 1 | 1330 | 0 | 0.0 | `trace-matter-d0e559d834` |

## 独有贡献溯源

逐条见 `eval/dual_unique_points.csv`；每行都带 `clause_id`、原文引文与 `risk_types`。
