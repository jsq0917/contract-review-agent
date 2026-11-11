# 引用补证离线对照（mock + citation-blind，无付费调用）

> 目的：验证 `resolve_citations` 在「上游完全没给引用」时能补回多少、花多少步数。
> 限制：mock Provider 的 token 恒为 0、成本为 null；**per-finding token 与费用无法由本脚本证明**，
> 需要真实 API 才能测量（未执行，未获批准）。

| case | 模式 | findings | 引用/未引用 | 引用覆盖率 | 补证 resolved/refused | 工具步数 | 补证耗时ms | 总耗时ms |
|---|---|---:|---|---:|---|---:|---:|---:|
| dev-synthetic-service | off | 4 | 0/4 | 0.0 | 0/0 | 0 | None | 578 |
| dev-synthetic-service | on | 4 | 4/0 | 1.0 | 4/0 | 8 | 603 | 798 |
| dev-synthetic-procurement | off | 5 | 0/5 | 0.0 | 0/0 | 0 | None | 465 |
| dev-synthetic-procurement | on | 5 | 5/0 | 1.0 | 5/0 | 10 | 617 | 812 |
| test-public-labor-template | off | 1 | 0/1 | 0.0 | 0/0 | 0 | None | 183 |
| test-public-labor-template | on | 1 | 1/0 | 1.0 | 1/0 | 2 | 597 | 793 |

## 逐条补证明细（mode=on）

| case | finding | clause | 审查路 | 状态 | 工具步数 | 模型调用 | prompt tokens | completion tokens | 耗时ms | 法条 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---|
| dev-synthetic-service | F-legal-C0002-1 | C0002 | legal | resolved | 2 | 3 | 0 | 0 | 10 | 中华人民共和国劳动法 第三十四条 |
| dev-synthetic-service | F-legal-C0004-1 | C0004 | legal | resolved | 2 | 3 | 0 | 0 | 8 | 中华人民共和国民法典 第五百八十五条 |
| dev-synthetic-service | F-legal-C0005-1 | C0005 | legal | resolved | 2 | 3 | 0 | 0 | 7 | 中华人民共和国民法典 第五百六十六条 |
| dev-synthetic-service | F-legal-C0006-1 | C0006 | legal | resolved | 2 | 3 | 0 | 0 | 7 | 中华人民共和国劳动法 第八十四条 |
| dev-synthetic-procurement | F-legal-C0002-1 | C0002 | legal | resolved | 2 | 3 | 0 | 0 | 10 | 中华人民共和国民法典 第四百一十六条 |
| dev-synthetic-procurement | F-legal-C0003-1 | C0003 | legal | resolved | 2 | 3 | 0 | 0 | 6 | 中华人民共和国劳动法 第八十二条 |
| dev-synthetic-procurement | F-legal-C0005-1 | C0005 | legal | resolved | 2 | 3 | 0 | 0 | 7 | 中华人民共和国民法典 第八百七十三条 |
| dev-synthetic-procurement | F-legal-C0005-2 | C0005 | legal | resolved | 2 | 3 | 0 | 0 | 7 | 中华人民共和国民法典 第八百七十三条 |
| dev-synthetic-procurement | F-legal-C0007-1 | C0007 | legal | resolved | 2 | 3 | 0 | 0 | 6 | 中华人民共和国劳动法 第八十四条 |
| test-public-labor-template | F-legal-C0005-1 | C0005 | legal | resolved | 2 | 3 | 0 | 0 | 14 | 中华人民共和国劳动合同法 第四十七条 |

## 结论与降级

- 离线可证明：补证能把「零引用」恢复到可核对引用；本轮数字见上表。
- 离线不可证明：真实 API 的 per-finding token、补证带来的费用增量、真实延迟分布。
  因此「补证成本可控」不作为当前可宣称能力，只说「有轮次/步数/条数上限」。
