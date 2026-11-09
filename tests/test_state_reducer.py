from datetime import date

from lexflow.graph.state import merge_duplicate_findings, merge_evidence, merge_findings
from lexflow.models import Evidence, Finding


def make_evidence(evidence_id: str, score: float) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        law_name="中华人民共和国民法典",
        article_no="第五百八十五条",
        quote="当事人可以约定违约金。",
        effective_from=date(2021, 1, 1),
        effective_to=None,
        source_url="https://flk.npc.gov.cn/",
        version_id="民法典-2021",
        score=score,
    )


def make_finding(
    finding_id: str,
    clause_id: str = "C0002",
    reviewer: str = "legal",
    risk_type: str = "payment",
    risk_level: str = "medium",
    issue: str | None = None,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        clause_id=clause_id,
        reviewer=reviewer,  # type: ignore[arg-type]
        risk_level=risk_level,  # type: ignore[arg-type]
        issue=issue or f"{reviewer} 视角的问题",
        suggestion=f"{reviewer} 视角的建议",
        original_quote="甲方应在收到发票后 90 日内付款。",
        risk_type=risk_type,
    )


def test_merge_evidence_dedup_by_law_and_article():
    left = [make_evidence("E001", 0.2)]
    right = [make_evidence("E002", 0.9)]
    merged = merge_evidence(left, right)
    assert len(merged) == 1
    assert merged[0].evidence_id == "E002"
    assert merged[0].score == 0.9


# ---------------------------------------------------------------- findings


def test_merge_findings_merges_cross_reviewer_duplicates():
    """同一 (clause_id, risk_type) 的两路发现必须合成一条，且来源两路都记。"""
    left = [make_finding("F-legal-C0002-1", reviewer="legal")]
    right = [make_finding("F-commercial-C0002-1", reviewer="commercial")]

    merged = merge_findings(left, right)

    assert len(merged) == 1
    assert merged[0].reviewers == ["legal", "commercial"]
    # 同风险等级时法务为主条目
    assert merged[0].reviewer == "legal"


def test_merge_findings_keeps_higher_risk_level_as_primary():
    left = [make_finding("F-legal-C0004-1", clause_id="C0004", risk_type="penalty_cap", risk_level="medium")]
    right = [
        make_finding(
            "F-commercial-C0004-1",
            clause_id="C0004",
            reviewer="commercial",
            risk_type="penalty_cap",
            risk_level="high",
        )
    ]

    merged = merge_findings(left, right)

    assert len(merged) == 1
    assert merged[0].risk_level == "high"
    assert merged[0].reviewer == "commercial"


def test_merge_findings_keeps_distinct_risk_types_apart():
    """同一条款上两路报的是不同风险，不该合并。"""
    left = [make_finding("F-legal-C0002-1", risk_type="payment")]
    right = [make_finding("F-commercial-C0002-1", reviewer="commercial", risk_type="delivery")]

    merged = merge_findings(left, right)

    assert len(merged) == 2


def test_merge_findings_does_not_merge_empty_risk_type():
    """risk_type 为空时没有可靠合并键，宁可留两条也不压成一条。"""
    left = [make_finding("F-legal-C0002-1", risk_type="")]
    right = [make_finding("F-commercial-C0002-1", reviewer="commercial", risk_type="")]

    merged = merge_findings(left, right)

    assert len(merged) == 2


def test_merge_findings_is_idempotent_after_merge():
    """合并结果再喂回 reducer 不能又变多——这是 bind_evidence 回写时的真实路径。"""
    left = [make_finding("F-legal-C0002-1", reviewer="legal")]
    right = [make_finding("F-commercial-C0002-1", reviewer="commercial")]

    once = merge_findings(left, right)
    twice = merge_findings(once, once)
    thrice = merge_findings(twice, [])

    assert len(once) == len(twice) == len(thrice) == 1
    assert thrice[0].reviewers == ["legal", "commercial"]


def test_merge_duplicate_findings_concatenates_divergent_issues():
    """两路对同一风险点的表述不同时，合并后两边的说法都要保留。"""
    left = [make_finding("F-legal-C0002-1", reviewer="legal", issue="付款期限过长")]
    right = [
        make_finding(
            "F-commercial-C0002-1",
            reviewer="commercial",
            issue="资金占用成本高",
        )
    ]

    merged = merge_duplicate_findings(left + right)

    assert len(merged) == 1
    assert "[法务] 付款期限过长" in merged[0].issue
    assert "[商务] 资金占用成本高" in merged[0].issue
