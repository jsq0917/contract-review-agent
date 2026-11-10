"""五道核验闸：纯函数，便于单测与离线复现。

G1 条号存在：引用必须命中证据账本中的真实条号。
G2 逐字引文：引用文本与账本原文归一化后必须逐字一致。
G3 时效：as_of 必须在生效区间内。
G4 语境支撑：条款原文必须命中规则的判定要素、且不命中排除项。
G5 数值一致：finding 里的数值断言必须能在条款原文或所引法条中找到出处。

每条闸返回 pass / fail / skipped 三态。skipped 表示「当前输入下无法判定」，
既不算通过也不算失败，主要用于两类情况：

- API 引擎不产出 ``rule_id``：G4 只能确认存在引文，不能校验法条是否适用，
  因此判 skipped，不再伪装成 pass；
- G5 遇到换算值（如按日千分之五折合年化 182.5%）：按原条款比率复核后判
  skipped，表示「数算对了，但不是原文里直接出现的值」。

G4 挡的是「引对了条文但支撑不了结论」。它只在有规则 ID 时真正生效；
**G4 至今不判断法条适用性**，文档里不得写成「已校验法条是否适用」。
"""

from __future__ import annotations

import re
from datetime import date

from lexflow.models import Evidence, Finding, Verification, VerificationStatus

from lexflow.review.rules import RULES_BY_ID

PUNCT_RE = re.compile(r"\s+|[，。；：、,.!?;:\"'“”‘’（）()《》【】\[\]…—\-]")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
PERCENT_RE = re.compile(r"百分之([零〇一二两三四五六七八九十百千]+|\d+(?:\.\d+)?)")
PERMILLE_RE = re.compile(r"千分之([零〇一二两三四五六七八九十百千]+|\d+(?:\.\d+)?)")
DAILY_PERCENT_RE = re.compile(r"每日\s*(\d+(?:\.\d+)?)\s*%")
DERIVE_HINTS = ("年化", "折合", "相当于", "换算")

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def normalize_quote(text: str) -> str:
    return PUNCT_RE.sub("", text or "")


def _parse_number(token: str) -> float | None:
    """把「80」或「八十」「五百」这类 token 转成数值；解析失败返回 None。"""
    token = (token or "").strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        pass
    section = 0
    number = 0
    seen = False
    for char in token:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
            seen = True
        elif char in _CN_UNITS:
            unit = _CN_UNITS[char]
            section += (number or 1) * unit
            number = 0
            seen = True
        else:
            return None
    return float(section + number) if seen else None


def _source_values(text: str) -> set[float]:
    """抽取文本里的数值，并把「百分之X / 千分之X」换算成百分数。

    千分之五 -> 0.5（即 0.5%），这样 finding 里写的 ``182.5`` 才能和
    ``0.5 × 365`` 对上，而不是去原文里找字面上的 ``5``。
    """
    values: set[float] = set()
    for match in PERCENT_RE.finditer(text):
        number = _parse_number(match.group(1))
        if number is not None:
            values.add(round(number, 6))
    for match in PERMILLE_RE.finditer(text):
        number = _parse_number(match.group(1))
        if number is not None:
            values.add(round(number / 10, 6))
    for match in DAILY_PERCENT_RE.finditer(text):
        values.add(round(float(match.group(1)), 6))
    stripped = PERCENT_RE.sub("", PERMILLE_RE.sub("", DAILY_PERCENT_RE.sub("", text)))
    for match in NUM_RE.finditer(stripped):
        values.add(round(float(match.group()), 6))
    return values


def _asserted_numbers(issue: str) -> list[str]:
    """issue 里需要溯源的数值；「第585条」这类条号不参与。"""
    return [
        match.group()
        for match in NUM_RE.finditer(issue)
        if not issue[: match.start()].endswith("第")
    ]


def _found(value: str, sources: list[str], values: set[float]) -> bool:
    if any(value in source for source in sources):
        return True
    try:
        number = float(value)
    except ValueError:
        return False
    return any(abs(number - item) < 1e-6 for item in values)


def _derived(value: str, original_quote: str, issue: str) -> bool:
    """换算值：issue 明确写了「年化/折合/相当于/换算」，且能由原条款比率复算。"""
    if not any(hint in issue for hint in DERIVE_HINTS):
        return False
    try:
        target = float(value)
    except ValueError:
        return False
    rates: set[float] = set()
    for match in PERMILLE_RE.finditer(original_quote):
        number = _parse_number(match.group(1))
        if number is not None:
            rates.add(number / 10)
    for match in PERCENT_RE.finditer(original_quote):
        number = _parse_number(match.group(1))
        if number is not None:
            rates.add(number)
    for match in DAILY_PERCENT_RE.finditer(original_quote):
        rates.add(float(match.group(1)))
    return any(abs(rate * 365 - target) <= 0.05 for rate in rates)


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


def g4_grounded(finding: Finding) -> tuple[VerificationStatus, str]:
    """用规则本身复判一次「这句话是否真的构成该风险」。

    排除项优先于判定项：条款命中排除项时，即使也命中了 detector，也算不成立——
    这正是 R-PEN-001 曾把「累计不超过合同总价百分之五」误报的那类情况。

    没有 ``rule_id``（API 引擎）时无法复判，返回 skipped 而不是 pass：
    只确认「模型填了引文」不等于「引文适用」。
    """
    rule = RULES_BY_ID.get(finding.rule_id or "")
    if rule is None:
        if finding.cited_quote:
            return "skipped", "无规则 ID（API 引擎）：G4 不校验法条适用性，仅确认存在引文"
        return "fail", "无规则 ID 且无引文，无法判定语境支撑"

    text = finding.original_quote or ""
    for pattern in (*rule.clause_exclusions, *rule.exclusions):
        if pattern.search(text):
            return "fail", f"条款命中规则 {rule.rule_id} 的排除项，该风险不成立"
    if any(pattern.search(text) for pattern in rule.detectors):
        return "pass", f"条款命中规则 {rule.rule_id} 的判定要素"
    return "fail", f"条款未命中规则 {rule.rule_id} 的判定要素"


def g5_numeric_consistent(finding: Finding, evidence_quote: str = "") -> tuple[VerificationStatus, str]:
    """finding 的 issue 里若出现数值，必须能在条款原文或所引法条中找到同一个数。

    三种结果：
    - pass：数值在 original_quote 或 evidence_quote 中（含「百分之八十」这类中文数字）；
    - skipped：数值是换算值（issue 写了年化/折合），且能由条款里的比率复算；
    - fail：既找不到出处，也无法复算。

    「第 X 条」这类条号数字不参与检查（前一个字是「第」）。
    """
    issue = finding.issue or ""
    asserted = _asserted_numbers(issue)
    if not asserted:
        return "pass", "issue 无数值断言，G5 不适用"

    sources = [finding.original_quote or "", evidence_quote or ""]
    values = set()
    for source in sources:
        values |= _source_values(source)

    missing: list[str] = []
    derived: list[str] = []
    for value in asserted:
        if _found(value, sources, values):
            continue
        if _derived(value, finding.original_quote or "", issue):
            derived.append(value)
            continue
        missing.append(value)

    if missing:
        return "fail", f"issue 中数值在条款原文与所引法条中找不到出处：{', '.join(missing)}"
    if derived:
        return (
            "skipped",
            f"issue 中数值为换算值，已按原条款比率复核：{', '.join(derived)}；不是原文直接给出的数",
        )
    return "pass", "issue 中数值均可在条款原文或所引法条中找到"


def _verification(finding_id: str, gate: str, status: VerificationStatus, detail: str) -> Verification:
    return Verification(
        finding_id=finding_id,
        gate=gate,  # type: ignore[arg-type]
        passed=status == "pass",
        status=status,
        detail=detail,
    )


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
    results.append(_verification(finding.finding_id, "G1", "pass" if g1_passed else "fail", g1_detail))
    if evidence is not None:
        g2_passed, g2_detail = g2_verbatim(finding.cited_quote, evidence.quote)
        g3_passed, g3_detail = g3_effective(as_of, evidence.effective_from, evidence.effective_to)
    else:
        g2_passed, g2_detail = False, "无对应证据，G2 不通过"
        g3_passed, g3_detail = False, "无对应证据，G3 不通过"
    results.append(_verification(finding.finding_id, "G2", "pass" if g2_passed else "fail", g2_detail))
    results.append(_verification(finding.finding_id, "G3", "pass" if g3_passed else "fail", g3_detail))

    g4_status, g4_detail = g4_grounded(finding)
    results.append(_verification(finding.finding_id, "G4", g4_status, g4_detail))
    evidence_quote = evidence.quote if evidence is not None else ""
    g5_status, g5_detail = g5_numeric_consistent(finding, evidence_quote)
    results.append(_verification(finding.finding_id, "G5", g5_status, g5_detail))
    return results
