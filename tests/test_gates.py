from datetime import date

from lexflow.models import Evidence, Finding
from lexflow.verify.gates import (
    g1_article_exists,
    g2_verbatim,
    g3_effective,
    g4_grounded,
    g5_numeric_consistent,
    verify_finding,
)


def make_evidence() -> Evidence:
    return Evidence(
        evidence_id="E001",
        law_name="中华人民共和国民法典",
        article_no="第五百八十五条",
        quote="当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金。",
        effective_from=date(2021, 1, 1),
        effective_to=None,
        source_url="https://flk.npc.gov.cn/",
        version_id="民法典-2021",
    )


def make_finding(**overrides) -> Finding:
    base = dict(
        finding_id="F001",
        clause_id="C0001",
        reviewer="legal",
        risk_level="high",
        issue="违约金",
        suggestion="调整",
        original_quote="违约金按日千分之五",
        cited_quote=make_evidence().quote,
        law_name="中华人民共和国民法典",
        article_no="第五百八十五条",
    )
    base.update(overrides)
    return Finding(**base)


def test_g1_fabricated_article_fails():
    ledger = [make_evidence()]
    passed, detail = g1_article_exists("中华人民共和国民法典", "第九千九百九十九条", ledger)
    assert not passed
    assert "不存在" in detail


def test_g2_tampered_quote_fails():
    ledger = [make_evidence()]
    finding = make_finding(cited_quote="违约金一律无效。")
    results = verify_finding(finding, ledger, date(2026, 11, 3))
    g2 = next(item for item in results if item.gate == "G2")
    assert not g2.passed


def test_g3_expired_article_fails():
    evidence = make_evidence()
    evidence.effective_to = date(2020, 12, 31)
    passed, detail = g3_effective(date(2026, 11, 3), evidence.effective_from, evidence.effective_to)
    assert not passed
    assert "晚于失效日" in detail


def test_all_gates_pass_for_verbatim_current_article():
    ledger = [make_evidence()]
    finding = make_finding()
    results = verify_finding(finding, ledger, date(2026, 11, 3))
    assert all(item.passed for item in results)
    assert g2_verbatim(finding.cited_quote, ledger[0].quote)[0]


def test_verify_finding_emits_five_gates():
    results = verify_finding(make_finding(), [make_evidence()], date(2026, 11, 3))
    assert [item.gate for item in results] == ["G1", "G2", "G3", "G4", "G5"]


# ------------------------------------------------------------------ G4 语境支撑


def test_g4_passes_when_clause_hits_detector():
    """条款确实写了「违约金 + 按日/千分之」，规则判定要素成立。"""
    finding = make_finding(
        rule_id="R-PEN-001",
        original_quote="违约金每延迟一日按千分之五计算。",
    )
    passed, detail = g4_grounded(finding)
    assert passed
    assert "R-PEN-001" in detail


def test_g4_blocks_capped_penalty_false_positive():
    """条款明确写了累计上限，即使命中「每日+千分之」也不该报——排除项优先。"""
    finding = make_finding(
        rule_id="R-PEN-001",
        original_quote="违约金每日按合同总价的千分之三计算，累计不超过合同总价的百分之五。",
    )
    passed, detail = g4_grounded(finding)
    assert not passed
    assert "排除项" in detail


def test_g4_without_rule_id_degrades_to_citation_check():
    """API 引擎不产出 rule_id，G4 退化为引文存在性检查，并在 detail 写明口径。"""
    finding = make_finding(rule_id=None, cited_quote="当事人可以约定违约金。")
    passed, detail = g4_grounded(finding)
    assert passed
    assert "API 引擎" in detail


def test_g4_without_rule_id_and_quote_fails():
    finding = make_finding(rule_id=None, cited_quote="")
    passed, _ = g4_grounded(finding)
    assert not passed


# ------------------------------------------------------------------ G5 数值一致


def test_g5_passes_when_numbers_traceable():
    finding = make_finding(issue="违约金按 5% 计算且无上限", original_quote="违约金按 5% 计算。")
    passed, _ = g5_numeric_consistent(finding)
    assert passed


def test_g5_blocks_fabricated_number():
    finding = make_finding(issue="违约金按 8% 计算且无上限", original_quote="违约金按 5% 计算。")
    passed, detail = g5_numeric_consistent(finding)
    assert not passed
    assert "8" in detail


def test_g5_ignores_article_number_prefix():
    """「第585条」是条号，不是需要溯源的数值断言。"""
    finding = make_finding(issue="依据第585条，该违约金条款过高", original_quote="违约金按日计算。")
    passed, _ = g5_numeric_consistent(finding)
    assert passed


def test_g5_not_applicable_without_numbers():
    finding = make_finding(issue="违约金未设上限", original_quote="违约金按日计算。")
    passed, detail = g5_numeric_consistent(finding)
    assert passed
    assert "不适用" in detail
