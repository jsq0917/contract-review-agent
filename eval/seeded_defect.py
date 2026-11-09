"""种子缺陷评测：五道闸的双向度量。

做法：
1. 跑示例合同，拿到一批**正确** findings（mock 规则产出，且已人工核对过风险条款）；
2. 对每条 finding 程序化植入 8 类错误，逐条过五道闸，统计检出率；
3. 原样跑一遍对照组，统计正确 finding 被误判 fail 的比例（误报率）。

**只报检出率是耍流氓**：把闸门收紧到「什么都 fail」就能 100%。两个数必须一起报。

局限：样本来自 2 份自造合同（9 条 findings），缺陷是程序植入而非真实错案；
结论只说明「这五道闸能否拦住这 8 类机械错误」，不代表真实法律质量。
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lexflow.models import Evidence, Finding
from lexflow.service import AnalyzeService
from lexflow.verify.gates import verify_finding

GOLD_FILES = (
    ROOT / "eval" / "gold.jsonl",
    ROOT / "data" / "synthetic" / "gold.jsonl",
)
OUT_MD = ROOT / "eval" / "seeded_defect_results.md"
OUT_JSON = ROOT / "eval" / "seeded_defect_results.json"

#: 与法条无关的通用条款，用来制造「条款不支撑该结论」。
IRRELEVANT_QUOTE = "本合同一式两份，甲乙双方各执一份，具有同等法律效力。"
#: 明确写了累计上限的违约金条款，用来制造「有上限却报成无上限」。
CAPPED_PENALTY_QUOTE = "违约金每日按合同总价的千分之三计算，累计不超过合同总价的百分之五。"
#: 原文不会出现的数值，用来制造「金额算错」。
FABRICATED_PERCENT = "8"


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def collect_samples(service: AnalyzeService, gold_files: tuple[Path, ...]) -> list[dict]:
    """跑示例合同与合成合同，收集 findings / evidence / as_of。"""
    samples: list[dict] = []
    for gold_path in gold_files:
        for row in load_rows(gold_path):
            relative = row.get("sample")
            path = ROOT / relative if relative else gold_path.parent / row["file"]
            if not path.exists():
                continue
            result = service.analyze(
                path,
                stance=row.get("stance", "甲方"),
                as_of=row.get("as_of"),
                engine="mock",
                require_confirmation=False,
                auto_approve=True,
            )
            report = result.report
            fields = set(Finding.model_fields)
            findings = [
                Finding(**{key: value for key, value in item.items() if key in fields})
                for item in report.get("findings", [])
            ]
            if not findings:
                continue
            evidence = [Evidence(**item) for item in report.get("evidence", [])]
            samples.append(
                {
                    "sample": str(path.relative_to(ROOT)),
                    "as_of": date.fromisoformat(row.get("as_of") or "2026-11-03"),
                    "findings": findings,
                    "evidence": evidence,
                }
            )
    return samples


def find_evidence(finding: Finding, ledger: list[Evidence]) -> Evidence | None:
    for item in ledger:
        if item.law_name == finding.law_name and item.article_no == finding.article_no:
            return item
    return None


def failed_gates(finding: Finding, ledger: list[Evidence], as_of: date) -> list[str]:
    return [item.gate for item in verify_finding(finding, ledger, as_of) if not item.passed]


# ------------------------------------------------------------------ 8 类植入


def mutate_fabricated_article(finding: Finding, evidence: Evidence | None, as_of: date):
    return finding.model_copy(update={"article_no": "第九千九百九十九条"}), as_of


def mutate_wrong_law(finding: Finding, evidence: Evidence | None, as_of: date):
    return finding.model_copy(update={"law_name": "中华人民共和国海商法"}), as_of


def mutate_tampered_quote(finding: Finding, evidence: Evidence | None, as_of: date):
    quote = (finding.cited_quote or "").rstrip("。") + "，本条已废止。"
    return finding.model_copy(update={"cited_quote": quote}), as_of


def mutate_expired_as_of(finding: Finding, evidence: Evidence | None, as_of: date):
    """把 as_of 挪到该法条生效前一天，制造「引用尚未生效的版本」。"""
    if evidence is None:
        return finding, as_of
    return finding, evidence.effective_from - timedelta(days=1)


def mutate_unsupported_clause(finding: Finding, evidence: Evidence | None, as_of: date):
    return finding.model_copy(update={"original_quote": IRRELEVANT_QUOTE}), as_of


def mutate_capped_penalty(finding: Finding, evidence: Evidence | None, as_of: date):
    return finding.model_copy(update={"original_quote": CAPPED_PENALTY_QUOTE}), as_of


def mutate_wrong_number(finding: Finding, evidence: Evidence | None, as_of: date):
    issue = f"{finding.issue.rstrip('。')}，按 {FABRICATED_PERCENT}% 计算。"
    return finding.model_copy(update={"issue": issue}), as_of


def mutate_missing_citation(finding: Finding, evidence: Evidence | None, as_of: date):
    return finding.model_copy(update={"law_name": None, "article_no": None}), as_of


MUTATIONS: tuple[dict, ...] = (
    {"name": "编造条号", "expect": "G1", "apply": mutate_fabricated_article, "only_rule": None},
    {"name": "张冠李戴法名", "expect": "G1", "apply": mutate_wrong_law, "only_rule": None},
    {"name": "篡改引文", "expect": "G2", "apply": mutate_tampered_quote, "only_rule": None},
    {"name": "引用未生效版本", "expect": "G3", "apply": mutate_expired_as_of, "only_rule": None},
    {"name": "条款不支撑结论", "expect": "G4", "apply": mutate_unsupported_clause, "only_rule": None},
    {"name": "有上限报成无上限", "expect": "G4", "apply": mutate_capped_penalty, "only_rule": "R-PEN-001"},
    {"name": "金额/比例算错", "expect": "G5", "apply": mutate_wrong_number, "only_rule": None},
    {"name": "无依据（缺引用）", "expect": "G1", "apply": mutate_missing_citation, "only_rule": None},
)


def main() -> int:
    service = AnalyzeService()
    samples = collect_samples(service, GOLD_FILES)

    total_findings = sum(len(item["findings"]) for item in samples)
    if total_findings == 0:
        raise SystemExit("未产出 findings，无法评测")

    # 对照组：原始正确 findings 过闸，用来算误报率
    false_positive: list[dict] = []
    for sample in samples:
        for finding in sample["findings"]:
            bad = failed_gates(finding, sample["evidence"], sample["as_of"])
            if bad:
                false_positive.append(
                    {"sample": sample["sample"], "finding_id": finding.finding_id, "failed": bad}
                )

    # 实验组：逐类植入
    detail: list[dict] = []
    for mutation in MUTATIONS:
        variants = 0
        caught_any = 0
        caught_expected = 0
        misses: list[dict] = []
        for sample in samples:
            for finding in sample["findings"]:
                if mutation["only_rule"] and finding.rule_id != mutation["only_rule"]:
                    continue
                evidence = find_evidence(finding, sample["evidence"])
                mutated, as_of = mutation["apply"](finding, evidence, sample["as_of"])
                bad = failed_gates(mutated, sample["evidence"], as_of)
                variants += 1
                if bad:
                    caught_any += 1
                if mutation["expect"] in bad:
                    caught_expected += 1
                else:
                    misses.append(
                        {
                            "sample": sample["sample"],
                            "finding_id": finding.finding_id,
                            "rule_id": finding.rule_id,
                            "got": bad,
                        }
                    )
        detail.append(
            {
                "name": mutation["name"],
                "expect": mutation["expect"],
                "variants": variants,
                "caught_any": caught_any,
                "caught_expected": caught_expected,
                "misses": misses,
            }
        )

    total_variants = sum(item["variants"] for item in detail)
    total_caught = sum(item["caught_expected"] for item in detail)

    payload = {
        "samples": [item["sample"] for item in samples],
        "total_findings": total_findings,
        "total_variants": total_variants,
        "total_caught_by_expected_gate": total_caught,
        "false_positive": false_positive,
        "detail": detail,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 种子缺陷评测（五道闸双向度量）",
        "",
        f"> 样本：{len(samples)} 份合同（`eval/gold.jsonl` 示例 + `data/synthetic/gold.jsonl` 合成），"
        f"mock 规则产出 **{total_findings}** 条 findings；缺陷为**程序植入**，不是真实错案。",
        "> 结论只说明「这五道闸能否拦住这 8 类机械错误」，不代表真实法律质量。",
        "",
        "## 检出率（按期望闸判定）",
        "",
        "| 植入错误 | 期望闸 | 变体数 | 期望闸拦下 | 检出率 | 任一闸拦下 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for item in detail:
        rate = f"{item['caught_expected'] / item['variants']:.2f}" if item["variants"] else "—"
        lines.append(
            f"| {item['name']} | {item['expect']} | {item['variants']} | "
            f"{item['caught_expected']} | {rate} | {item['caught_any']} |"
        )
    overall = f"{total_caught / total_variants:.4f}" if total_variants else "—"
    lines += [
        f"| **合计** | — | **{total_variants}** | **{total_caught}** | **{overall}** | — |",
        "",
        "## 误报率（对照组）",
        "",
        f"- 正确 findings：**{total_findings}** 条",
        f"- 被判 fail：**{len(false_positive)}** 条（误报率 {len(false_positive) / total_findings:.4f}）",
    ]
    if false_positive:
        for item in false_positive:
            lines.append(f"  - {item['sample']} · {item['finding_id']} · 失败闸 {item['failed']}")
    lines.append("")

    misses = [item for item in detail if item["misses"]]
    if misses:
        lines.append("## 漏检明细")
        for item in misses:
            lines.append(f"### {item['name']}（期望 {item['expect']}）")
            for miss in item["misses"]:
                lines.append(
                    f"- {miss['sample']} · {miss['finding_id']} · rule={miss['rule_id']} · 实际失败闸 {miss['got']}"
                )
        lines.append("")
    else:
        lines.append("## 漏检明细\n\n无：8 类植入错误全部被期望闸拦下。\n")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_MD.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
