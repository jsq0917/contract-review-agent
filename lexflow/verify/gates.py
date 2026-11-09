"""五道核验闸：纯函数，便于单测与离线复现。

G1 条号存在：引用必须命中证据账本中的真实条号。
G2 逐字引文：引用文本与账本原文归一化后必须逐字一致。
G3 时效：as_of 必须在生效区间内。
G4 语境支撑：条款原文必须命中规则的判定要素、且不命中排除项。
G5 数值一致：finding 里的数值断言必须能在条款原文中找到出处。

G4 挡的是「引对了条文但支撑不了结论」——真实法律场景里比编造条号更危险的
失败模式（条号真、引文真，但这条法条不是这个意思）。G4/G5 都是纯规则，
不调模型：规则的排除项本身就是「这个写法不构成风险」的显式定义。
"""

from __future__ import annotations

import re
from datetime import date

from lexflow.models import Evidence, Finding, Verification
from lexflow.review.rules import RULES_BY_ID

PUNCT_RE = re.compile(r"\s+|[，。；：、,.!?;:\"'“”‘’（）()《》【】\[\]…—\-]")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def normalize_quote(text: str) -> str:
    return PUNCT_RE.sub("", text or "")


def g1_article_exists(law_name: str | None, article_no: str | None, ledger: list[Evidence]) -> tuple[bool, str]:
    if not law_name or not article_no:
        return False, "未提供法条引用"
    for evidence in ledger:
        if evidence.law_name == law_name and evidence.article_no == article_no:
            return True, f"命中 {law_name} {article_no}"
    return False, f"证据账本中不存在 {law_name} {article_no}"


def g2_verbatim(cited_quote: str, evidence_quote: str) -> tuple[bool, str]:
    if not cited_quote:
        return False, "未提供引文，无法做逐字核验"
    if normalize_quote(cited_quote) == normalize_quote(evidence_quote):
        return True, "引文与原文逐字一致（忽略空白与标点）"
    return False, "引文与原文不一致"


def g3_effective(as_of: date, effective_from: date, effective_to: date | None) -> tuple[bool, str]:
    if as_of < effective_from:
        return False, f"as_of {as_of} 早于生效日 {effective_from}"
    if effective_to is not None and as_of > effective_to:
        return False, f"as_of {as_of} 晚于失效日 {effective_to}"
    return True, f"as_of {as_of} 在生效区间内"


def g4_grounded(finding: Finding) -> tuple[bool, str]:
    """用规则本身复判一次「这句话是否真的构成该风险」。

    排除项优先于判定项：条款命中排除项时，即使也命中了 detector，也算不成立——
    这正是 R-PEN-001 曾把「累计不超过合同总价百分之五」误报的那类情况。
    """
    rule = RULES_BY_ID.get(finding.rule_id or "")
    if rule is None:
        # API 引擎不产出 rule_id，退化为引文存在性检查，并在 detail 里写明口径。
        if finding.cited_quote:
            return True, "无规则 ID（API 引擎），G4 退化为引文存在性检查"
        return False, "无规则 ID 且无引文，无法判定语境支撑"

    text = finding.original_quote or ""
    for pattern in (*rule.clause_exclusions, *rule.exclusions):
        if pattern.search(text):
            return False, f"条款命中规则 {rule.rule_id} 的排除项，该风险不成立"
    if any(pattern.search(text) for pattern in rule.detectors):
        return True, f"条款命中规则 {rule.rule_id} 的判定要素"
    return False, f"条款未命中规则 {rule.rule_id} 的判定要素"


def g5_numeric_consistent(finding: Finding) -> tuple[bool, str]:
    """finding 的 issue 里若出现数值，必须能在条款原文里找到同一个数。

    「第 X 条」这类条号数字不参与检查（前一个字是「第」）。
    """
    issue = finding.issue or ""
    asserted = [
        match.group()
        for match in NUM_RE.finditer(issue)
        if not issue[: match.start()].endswith("第")
    ]
    if not asserted:
        return True, "issue 无数值断言，G5 不适用"

    source = finding.original_quote or ""
    missing = [value for value in asserted if value not in source]
    if missing:
        return False, f"issue 中数值在条款原文找不到出处：{', '.join(missing)}"
    return True, "issue 中数值均可在条款原文找到"


def verify_finding(finding: Finding, ledger: list[Evidence], as_of: date) -> list[Verification]:
    evidence = None
    if finding.law_name and finding.article_no:
        matching = [
            item
            for item in ledger
            if item.law_name == finding.law_name and item.article_no == finding.article_no
        ]
        evidence = matching[0] if matching else None

    results: list[Verification] = []
    g1_passed, g1_detail = g1_article_exists(finding.law_name, finding.article_no, ledger)
    results.append(Verification(finding_id=finding.finding_id, gate="G1", passed=g1_passed, detail=g1_detail))
    if evidence is not None:
        g2_passed, g2_detail = g2_verbatim(finding.cited_quote, evidence.quote)
        g3_passed, g3_detail = g3_effective(as_of, evidence.effective_from, evidence.effective_to)
    else:
        g2_passed, g2_detail = False, "无对应证据，G2 不通过"
        g3_passed, g3_detail = False, "无对应证据，G3 不通过"
    results.append(Verification(finding_id=finding.finding_id, gate="G2", passed=g2_passed, detail=g2_detail))
    results.append(Verification(finding_id=finding.finding_id, gate="G3", passed=g3_passed, detail=g3_detail))

    g4_passed, g4_detail = g4_grounded(finding)
    results.append(Verification(finding_id=finding.finding_id, gate="G4", passed=g4_passed, detail=g4_detail))
    g5_passed, g5_detail = g5_numeric_consistent(finding)
    results.append(Verification(finding_id=finding.finding_id, gate="G5", passed=g5_passed, detail=g5_detail))
    return results
