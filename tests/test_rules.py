"""规则层回归：预付款子串误报、真实付款义务、规则表与法条引用。"""

from __future__ import annotations

from lexflow.config import ROOT
from lexflow.retrieval.corpus import load_corpus
from lexflow.review.rules import RULE_COVERAGE, RULES, RULES_BY_ID, detect_rules
from lexflow.synthetic import RISK_CLAUSES, TEMPLATES

CORPUS_FILE = ROOT / "data" / "corpus" / "law_articles.jsonl"


def _rule_ids(text: str, clause_type: str, stance: str = "甲方") -> list[str]:
    return [match.rule.rule_id for match in detect_rules(text, clause_type, stance)]


def test_procurement_prepayment_is_not_a_payment_risk() -> None:
    # 阶段 1 的误报来源：设备采购模板交付条款里的“预付款”曾被“付款”子串命中。
    delivery = TEMPLATES["proc"]["delivery"]
    assert "预付款" in delivery
    assert _rule_ids(delivery, "delivery") == []


def test_other_non_obligation_payment_phrases_are_excluded() -> None:
    for text in (
        "乙方已付款的，甲方应开具发票。",
        "乙方应在收到预付款后安排发货。",
        "甲方付款后交付，收款方为乙方。",
    ):
        assert _rule_ids(text, "payment") == [], text


def test_real_payment_obligation_still_hits() -> None:
    matches = detect_rules(RISK_CLAUSES["payment"], "payment")
    assert [match.rule.rule_id for match in matches] == ["R-PAY-001"]
    assert matches[0].quote
    # 只命中一次，避免同一条款重复生成 findings。
    assert len(matches) == 1


def test_rule_table_covers_required_categories_and_valid_citations() -> None:
    articles, mode = load_corpus(CORPUS_FILE)
    assert mode == "external"
    known = {(article.law_name, article.article_no) for article in articles}

    assert len(RULE_COVERAGE) == 9
    assert set(RULE_COVERAGE.values()) == {rule.rule_id for rule in RULES}
    for rule in RULES:
        assert rule.rule_id == RULES_BY_ID[rule.rule_id].rule_id
        assert (rule.law_name, rule.article_no) in known, rule.rule_id
        assert rule.detectors and rule.false_positive_boundary


def test_new_risk_rules_hit_and_safe_versions_do_not() -> None:
    cases = {
        "confidentiality_scope": "confidentiality",
        "force_majeure": "force_majeure",
        "liability_cap": "liability",
        "acceptance_standard": "delivery",
    }
    for clause_key, clause_type in cases.items():
        assert _rule_ids(RISK_CLAUSES[clause_key], clause_type) != [], clause_key

    from lexflow.synthetic import SAFE_CLAUSES

    for clause_key, clause_type in cases.items():
        assert _rule_ids(SAFE_CLAUSES[clause_key], clause_type) == [], clause_key


CAPPED_PENALTY = (
    "乙方逾期交货的，每逾期一日按合同总价千分之三支付违约金，累计不超过合同总价的百分之五。"
)
UNCAPPED_PENALTY = "乙方逾期交货的，每逾期一日按合同总价千分之五支付违约金，未设上限。"


def test_capped_penalty_is_not_a_penalty_cap_risk() -> None:
    # 阶段 3 事后发现的真实误报：有累计上限仍按“千分之”命中。
    assert _rule_ids(CAPPED_PENALTY, "liability") == []


def test_uncapped_high_penalty_still_hits() -> None:
    # 正交用例：无封顶表述的高额违约金必须继续命中，排除不能变成整条豁免。
    assert _rule_ids(UNCAPPED_PENALTY, "liability") == ["R-PEN-001"]
    for text in (
        "任何一方违约的，应按合同总额每日千分之五支付违约金，违约金不设上限。",
        "违约金按日千分之五计算，上不封顶。",
        "违约金按日千分之五计算，未封顶。",
    ):
        assert "R-PEN-001" in _rule_ids(text, "liability"), text


def test_stance_switches_unilateral_acceptance_direction() -> None:
    clause = "验收标准由乙方单方确定。"
    assert "R-ACC-001" in _rule_ids(clause, "delivery", "甲方")
    assert _rule_ids(clause, "delivery", "乙方") == []


def test_acceptance_right_is_only_a_risk_for_the_harmed_stance() -> None:
    clauses = (
        "甲方有权以任何理由拒绝验收，且拒绝验收不视为甲方违约。",
        "乙方有权以任何理由拒绝验收，且拒绝验收不视为乙方违约。",
    )
    # 甲方立场：单方条款均报；乙方立场：只报甲方一侧的验收特权。
    for clause in clauses:
        assert "R-ACC-001" in _rule_ids(clause, "delivery", "甲方"), clause
    assert "R-ACC-001" in _rule_ids(clauses[0], "delivery", "乙方")
    assert _rule_ids(clauses[1], "delivery", "乙方") == []


def test_stance_switches_unilateral_jurisdiction_direction() -> None:
    b_venue = "争议由乙方所在地人民法院管辖。"
    assert "R-JUR-001" in _rule_ids(b_venue, "jurisdiction", "甲方")
    assert _rule_ids(b_venue, "jurisdiction", "乙方") == []

    a_venue = "争议由甲方所在地人民法院管辖。"
    assert "R-JUR-001" in _rule_ids(a_venue, "jurisdiction", "乙方")


def test_stance_switches_payment_obligation_direction() -> None:
    a_pay = "甲方应在合同签订后五个工作日内支付全款。"
    b_pay = "乙方应在合同签订后五个工作日内支付全款。"
    assert "R-PAY-001" in _rule_ids(a_pay, "payment", "甲方")
    assert "R-PAY-001" not in _rule_ids(a_pay, "payment", "乙方")
    assert "R-PAY-001" in _rule_ids(b_pay, "payment", "乙方")
    assert "R-PAY-001" not in _rule_ids(b_pay, "payment", "甲方")


def test_late_payment_exemption_follows_stance() -> None:
    a_exempt = "甲方逾期付款的，不承担违约责任。"
    # 甲方立场：单方免责条款也报；乙方立场：该条直接损害乙方回款，必须报。
    assert "R-PAY-002" in _rule_ids(a_exempt, "liability", "甲方")
    assert "R-PAY-002" in _rule_ids(a_exempt, "liability", "乙方")

    b_exempt = "乙方逾期付款的，不承担违约责任。"
    # 乙方立场下，豁免乙方自身责任的条款不再报。
    assert "R-PAY-002" in _rule_ids(b_exempt, "liability", "甲方")
    assert _rule_ids(b_exempt, "liability", "乙方") == []
