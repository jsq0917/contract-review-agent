# 单路 / 双路对照结果

- 生成时间：2026-11-11T00:13:28.989120+00:00
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
| dev-synthetic-service | legal_only | 4 | 4 | 35607 | 0 | 0.0 | `trace-matter-2a29fdb2c4` |
| dev-synthetic-service | commercial_only | 4 | 4 | 1326 | 0 | 0.0 | `trace-matter-c124842d46` |
| dev-synthetic-service | both | 4 | 4 | 1760 | 0 | 0.0 | `trace-matter-ca5284d95a` |
| dev-synthetic-procurement | legal_only | 5 | 5 | 1865 | 0 | 0.0 | `trace-matter-0618380f41` |
| dev-synthetic-procurement | commercial_only | 5 | 5 | 1470 | 0 | 0.0 | `trace-matter-03c7bf5a24` |
| dev-synthetic-procurement | both | 5 | 5 | 2044 | 0 | 0.0 | `trace-matter-0a02bc09cd` |
| test-public-labor-template | legal_only | 1 | 1 | 1009 | 0 | 0.0 | `trace-matter-05fb482f43` |
| test-public-labor-template | commercial_only | 1 | 1 | 1072 | 0 | 0.0 | `trace-matter-1c7b11e54e` |
| test-public-labor-template | both | 1 | 1 | 1704 | 0 | 0.0 | `trace-matter-5caddb4fb6` |

## 独有贡献溯源

逐条见 `eval/dual_unique_points.csv`；每行都带 `clause_id`、原文引文与 `risk_types`。
