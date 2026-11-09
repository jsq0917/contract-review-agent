"""显式审查规则表。

本模块是 mock 引擎唯一的风险判定入口，也是规则说明的唯一来源。
每条规则包含：规则 ID、风险类型、适用条款类型、判定条件、命中后动作、
法条依据、误报边界，以及可执行的 detectors / exclusions 正则。

误报边界的核心约定：
- 付款类规则只把「我方（甲方/买方/需方/采购方/委托方）的付款义务」当作风险；
- 「收到预付款」「已付款」「收款」「付款后交付」等由对方收款或对方先行履行的表述不计为风险；
- 排除项在句级生效，避免一个句子里出现无关词就整条放过；
- 违约金上限的排除在条款级生效：只要条款明确写了累计上限/封顶，就整条不算超限风险；
- stance 只影响方向类规则（付款义务、单方验收标准、单方管辖）；
  甲方立场保留阶段 3 的「单方条款均报」语义，乙方立场只报甲方单方条款。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lexflow.models import RiskLevel

SENTENCE_SPLIT = re.compile(r"[。；;\n]+")


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    risk_type: str
    clause_types: tuple[str, ...]
    title: str
    condition: str
    action: str
    risk_level: RiskLevel
    issue: str
    suggestion: str
    law_name: str
    article_no: str
    false_positive_boundary: str
    detectors: tuple[re.Pattern[str], ...]
    exclusions: tuple[re.Pattern[str], ...] = field(default_factory=tuple)
    stance_detectors: dict[str, tuple[re.Pattern[str], ...]] = field(default_factory=dict)
    stance_exclusions: dict[str, tuple[re.Pattern[str], ...]] = field(default_factory=dict)
    clause_exclusions: tuple[re.Pattern[str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RuleMatch:
    rule: RuleSpec
    quote: str


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


_A_PARTY = r"(?:甲方|买方|需方|采购方|委托方)"
_B_PARTY = r"(?:乙方|卖方|供方|服务方|承包方|受托方)"
_ANY_PARTY = r"(?:甲方|买方|需方|采购方|委托方|乙方|卖方|供方|服务方|承包方|受托方)"
_OUR_PAYMENT_EXCLUSIONS = (
    _p(r"收到预付款"),
    _p(r"已付款"),
    _p(r"收款"),
    _p(r"付款后(?:交付|发货|履行)"),
)


def normalize_stance(stance: str | None) -> str:
    """把 API/CLI/UI 传入的立场归一为 甲方 / 乙方 / neutral。"""
    if stance in ("甲方", "乙方", "neutral"):
        return stance
    if stance in ("中立", "中性"):
        return "neutral"
    # 未传或未知值沿用甲方，保持既有调用方行为不变。
    return "甲方"


def _payment_detectors(our_party: str) -> tuple[re.Pattern[str], ...]:
    return (
        _p(rf"{our_party}[^。；\n]{{0,40}}(?:付款|支付|预付|结算)"),
        _p(r"(?:付款|支付)[^。；\n]{0,20}(?:百分之百|100%|全额)"),
        _p(r"合同签订后[^。；\n]{0,20}(?:付款|支付)"),
    )


def _payment_exclusions(excluded_party: str) -> tuple[re.Pattern[str], ...]:
    return _OUR_PAYMENT_EXCLUSIONS + (
        _p(rf"{excluded_party}[^。；\n]{{0,30}}(?:付款|支付|结算)"),
    )


def _venue_detectors(risk_party: str) -> tuple[re.Pattern[str], ...]:
    return (
        _p(
            rf"{risk_party}[^。；\n]{{0,10}}(?:所在地|住所地)"
            rf"[^。；\n]{{0,20}}(?:人民法院|法院|仲裁)"
        ),
        _p(
            rf"(?:由|在){risk_party}[^。；\n]{{0,10}}(?:所在地|住所地)"
            rf"[^。；\n]{{0,20}}(?:管辖|诉讼|仲裁)"
        ),
        _p(rf"{risk_party}(?:所在地|住所地)[^。；\n]{{0,10}}(?:管辖)"),
    )


def _acceptance_detectors(risk_party: str) -> tuple[re.Pattern[str], ...]:
    """只把 risk_party 一侧的验收特权/单方标准当作风险，供乙方立场使用。"""
    return (
        _p(rf"{risk_party}[^。；\n]{{0,30}}(?:任何理由|无理由)[^。；\n]{{0,10}}拒绝验收"),
        _p(
            rf"拒绝验收[^。；\n]{{0,20}}(?:不视为|不构成)[^。；\n]{{0,10}}"
            rf"{risk_party}[^。；\n]{{0,5}}(?:违约|责任)"
        ),
        _p(
            rf"验收[^。；\n]{{0,20}}(?:标准|条件)[^。；\n]{{0,10}}"
            rf"(?:由{risk_party}单方|{risk_party}单方)"
        ),
    )


def _late_payment_exemption_detectors(party: str) -> tuple[re.Pattern[str], ...]:
    """付款方逾期付款后单方免责；party 决定哪一方的免责被视为风险。"""
    return (
        _p(
            rf"{party}[^。；\n]{{0,30}}逾期(?:付款|支付)[^。；\n]{{0,20}}"
            rf"(?:不承担|无需承担|免于承担|免除|不视(?:为|作))[^。；\n]{{0,10}}"
            rf"(?:违约|责任|赔偿)"
        ),
    )


# 违约金「有上限」的正向表述。刻意不写裸「上限」/「封顶」：
# “不设上限 / 无上限 / 上不封顶 / 未封顶”都是无上限风险，不能被当成排除条件。
_PENALTY_CAP_EXCLUSIONS = (
    _p(r"累计不超过"),
    _p(r"最高不超过"),
    _p(r"累计(?:上限(?:为|不超过|是)|最高)"),
    _p(r"上限(?:为|不超过|是)"),
    _p(r"不超过合同(?:总价|总额|总金额)"),
    _p(r"(?<![不未])封顶"),
)



# 阶段 3 既有默认 detector：保持甲方视角下的已发布行为不变。
# 乙方视角只保留「甲方/买方一侧」的单方条款，避免把有利于乙方的条款也算风险。
_VENUE_DETECTORS_DEFAULT = (
    _p(
        r"(?:甲方|乙方|买方|卖方|供方|需方)[^。；\n]{0,10}(?:所在地|住所地)"
        r"[^。；\n]{0,20}(?:人民法院|法院|仲裁)"
    ),
    _p(
        r"(?:由|在)(?:甲方|乙方)[^。；\n]{0,10}(?:所在地|住所地)"
        r"[^。；\n]{0,20}(?:管辖|诉讼|仲裁)"
    ),
    _p(r"(?:甲方|乙方)(?:所在地|住所地)[^。；\n]{0,10}(?:管辖)"),
)

_ACCEPTANCE_DETECTORS_DEFAULT = (
    _p(r"(?:任何理由|无理由)[^。；\n]{0,10}拒绝验收"),
    _p(r"拒绝验收[^。；\n]{0,20}(?:不视为|不构成)[^。；\n]{0,10}(?:违约|责任)"),
    _p(
        r"验收[^。；\n]{0,20}(?:标准|条件)[^。；\n]{0,10}"
        r"(?:由甲方单方|甲方单方|由乙方单方|乙方单方)"
    ),
)



# 条款类型沿用 segmenter 的标签：payment/liability/termination/jurisdiction/
# confidentiality/force_majeure/delivery/general。
RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="R-PAY-001",
        risk_type="payment",
        clause_types=("payment", "delivery"),
        title="付款与交付顺序",
        condition="付款义务指向我方，且未与验收/交付绑定，或要求签约后短期全额付款。",
        action="生成付款风险 finding：要求把付款节点绑定验收结果，并保留先履行抗辩。",
        risk_level="medium",
        issue="付款节点未与验收/交付绑定，存在预付后无对价风险。",
        suggestion="将付款节点与验收结果绑定，明确先履行抗辩权，并保留验收不合格时的拒付/追回条款。",
        law_name="中华人民共和国民法典",
        article_no="第五百二十六条",
        false_positive_boundary=(
            "仅当付款义务指向我方（甲方/买方/需方/采购方/委托方）时命中；"
            "乙方/卖方/供方收款、收到预付款、已付款、付款后交付等不构成我方付款义务的表述不命中。"
        ),
        detectors=_payment_detectors(_A_PARTY),
        exclusions=_payment_exclusions(_B_PARTY),
        stance_detectors={
            "甲方": _payment_detectors(_A_PARTY),
            "乙方": _payment_detectors(_B_PARTY),
            "neutral": _payment_detectors(_A_PARTY) + _payment_detectors(_B_PARTY),
        },
        stance_exclusions={
            "甲方": _payment_exclusions(_B_PARTY),
            "乙方": _payment_exclusions(_A_PARTY),
            "neutral": _OUR_PAYMENT_EXCLUSIONS,
        },
    ),
    RuleSpec(
        rule_id="R-PAY-002",
        risk_type="payment_liability_exemption",
        clause_types=("payment", "liability", "delivery"),
        title="逾期付款免责",
        condition="约定付款方逾期付款不承担违约责任或免于赔偿。",
        action="生成逾期付款免责 finding：要求恢复逾期付款的违约责任与资金占用损失。",
        risk_level="high",
        issue="付款方逾期付款被免除违约责任，收款方的资金占用损失与催收成本缺乏救济。",
        suggestion="删除单方免责表述，约定逾期付款违约金或资金占用费，并保留催告与解除权。",
        law_name="中华人民共和国民法典",
        article_no="第五百七十七条",
        false_positive_boundary=(
            "仅当条款明确写明付款方逾期付款不承担违约责任/免于赔偿时命中；"
            "约定逾期付款按日计违约金、或按法定利率支付资金占用费的条款不命中。"
        ),
        detectors=_late_payment_exemption_detectors(_ANY_PARTY),
        stance_detectors={
            "甲方": _late_payment_exemption_detectors(_ANY_PARTY),
            "乙方": _late_payment_exemption_detectors(_A_PARTY),
            "neutral": _late_payment_exemption_detectors(_ANY_PARTY),
        },
    ),
    RuleSpec(
        rule_id="R-PEN-001",
        risk_type="penalty_cap",
        clause_types=("liability",),
        title="违约金上限",
        condition="约定按日/按总额比例计违约金，但未设上限或上限明显缺失。",
        action="生成违约金风险 finding：要求设置违约金上限并保留调整机制。",
        risk_level="high",
        issue="违约金按日/按比例计算且未设上限，可能被认定过高并引发调整争议。",
        suggestion="设置违约金累计上限（如合同总价的 20%–30%），并约定损失赔偿与违约金的衔接。",
        law_name="中华人民共和国民法典",
        article_no="第五百八十五条",
        false_positive_boundary=(
            "仅当出现“违约金”并伴随按日/千分之/不设上限等计算或上限缺失表述时命中；"
            "条款明确约定累计上限/封顶/不超过合同总价一定比例的，整条不命中；"
            "只约定直接损失赔偿、未出现违约金的条款不命中。"
        ),
        detectors=(
            _p(r"违约金[^。；\n]{0,40}(?:千分之|每日|按日)"),
            _p(r"违约金[^。；\n]{0,30}(?:不设上限|无上限|上不封顶)"),
            _p(r"(?:每日|按日)[^。；\n]{0,10}千分之"),
            _p(r"千分之[^。；\n]{0,40}违约金"),
        ),
        exclusions=(_p(r"(?:直接损失|实际损失)[^。；\n]{0,20}(?:为限|上限)"),),
        clause_exclusions=_PENALTY_CAP_EXCLUSIONS,
    ),
    RuleSpec(
        rule_id="R-TERM-001",
        risk_type="termination",
        clause_types=("termination",),
        title="解除条件",
        condition="授予一方无理由/随时/单方解除权，或解除后无需承担赔偿责任。",
        action="生成解除风险 finding：要求限定解除条件、通知期限与善后责任。",
        risk_level="high",
        issue="单方解除权表述过于宽泛，解除后不承担赔偿责任，权利义务明显失衡。",
        suggestion="限定解除触发条件与通知期限，明确解除后的结算、返还与损失赔偿。",
        law_name="中华人民共和国民法典",
        article_no="第五百六十三条",
        false_positive_boundary=(
            "仅当出现无理由/随时/任何时候/单方解除等表述时命中；"
            "以“严重违约且经催告”为前提的解除条款不命中。"
        ),
        detectors=(
            _p(r"(?:无理由|随时|任何时候|无需任何理由)[^。；\n]{0,20}(?:解除|终止)"),
            _p(r"单方[^。；\n]{0,20}(?:解除|终止)"),
            _p(r"(?:解除|终止)[^。；\n]{0,20}(?:无需|不承担)[^。；\n]{0,10}(?:赔偿|责任)"),
        ),
        exclusions=(
            _p(r"(?:严重违约|催告)[^。；\n]{0,40}(?:解除|终止)"),
            _p(r"(?:经催告|催告后)[^。；\n]{0,20}(?:解除|终止)"),
        ),
    ),
    RuleSpec(
        rule_id="R-JUR-001",
        risk_type="jurisdiction",
        clause_types=("jurisdiction",),
        title="管辖约定",
        condition="约定由一方所在地法院/仲裁机构管辖，未保留中立或对方所在地选项。",
        action="生成管辖风险 finding：提示核对管辖条款的明确性与对等性。",
        risk_level="medium",
        issue="管辖约定偏向一方所在地，需核对是否明确、是否对双方可执行。",
        suggestion="明确约定有管辖权的法院或仲裁机构，并核对级别管辖与仲裁条款的排他性。",
        law_name="中华人民共和国民法典",
        article_no="第四百七十条",
        false_positive_boundary=(
            "仅当约定由甲方或乙方单方所在地法院/仲裁且未出现合同签订地、被告所在地等中立选项时命中；"
            "本条只引用民法典第四百七十条（合同内容含争议解决方法），"
            "本地语料不含民事诉讼法，不能据此判断管辖条款的实体效力。"
        ),
        detectors=_VENUE_DETECTORS_DEFAULT,
        exclusions=(_p(r"(?:合同签订地|被告所在地|合同履行地)"),),
        stance_detectors={
            "甲方": _VENUE_DETECTORS_DEFAULT,
            "乙方": _venue_detectors(_A_PARTY),
            "neutral": _VENUE_DETECTORS_DEFAULT,
        },
    ),
    RuleSpec(
        rule_id="R-CONF-001",
        risk_type="confidentiality",
        clause_types=("confidentiality",),
        title="保密范围与期限",
        condition="约定永久/无限期保密，或把已公开信息纳入保密范围，或排除资料返还/销毁义务。",
        action="生成保密风险 finding：要求限定保密范围、期限与期满处理。",
        risk_level="medium",
        issue="保密范围或期限过宽，可能把已公开信息或永久义务强加于一方。",
        suggestion="限定保密信息范围与期限，约定保密资料返还或销毁，并保留法定披露例外。",
        law_name="中华人民共和国民法典",
        article_no="第五百零一条",
        false_positive_boundary=(
            "仅当出现永久/无限期保密、已公开信息纳入保密、无需返还/销毁等表述时命中；"
            "约定合理保密期限（如终止后两至三年）的条款不命中。"
        ),
        detectors=(
            _p(r"(?:永久|无限期)[^。；\n]{0,10}保密"),
            _p(r"保密[^。；\n]{0,20}(?:永久|无限期)"),
            _p(r"(?:已公开|公开信息)[^。；\n]{0,20}(?:保密|秘密)"),
            _p(r"(?:保密信息|保密资料)[^。；\n]{0,20}(?:不返还|无需返还|无需销毁|不予销毁)"),
        ),
        exclusions=(
            _p(r"保密期限为合同终止后[一二三四五六七八九十两0-9]+年"),
            _p(r"非公开"),
        ),
    ),
    RuleSpec(
        rule_id="R-FM-001",
        risk_type="force_majeure",
        clause_types=("force_majeure",),
        title="不可抗力免责",
        condition="排除不可抗力免责，或要求不可抗力下仍继续履行全部义务。",
        action="生成不可抗力风险 finding：要求恢复法定免责与顺延/解除机制。",
        risk_level="high",
        issue="条款排除不可抗力免责，可能违反法定免责规则并造成履行不能仍被追责。",
        suggestion="约定不可抗力通知、证明与顺延履行；持续超过约定期间可协商解除。",
        law_name="中华人民共和国民法典",
        article_no="第五百九十条",
        false_positive_boundary=(
            "仅当出现不得以不可抗力免责、即使不可抗力仍继续履行等表述时命中；"
            "约定通知、证明与协商延期履行的条款不命中。"
        ),
        detectors=(
            _p(r"(?:不得|无权)[^。；\n]{0,10}不可抗力[^。；\n]{0,20}(?:免责|免除)"),
            _p(r"即使[^。；\n]{0,10}不可抗力[^。；\n]{0,20}(?:仍|继续)"),
            _p(r"不可抗力[^。；\n]{0,20}(?:不予免责|不得免责|不适用免责)"),
        ),
        exclusions=(_p(r"(?:部分或者全部免除|免除责任|协商延期|顺延履行)"),),
    ),
    RuleSpec(
        rule_id="R-LIAB-001",
        risk_type="liability_cap",
        clause_types=("liability", "delivery"),
        title="责任限额",
        condition="约定无限赔偿，或把间接损失/利润损失纳入赔偿范围而不设上限。",
        action="生成责任限额风险 finding：要求设置赔偿上限并排除间接损失。",
        risk_level="high",
        issue="赔偿责任无上限且覆盖间接损失/利润损失，风险敞口不可控。",
        suggestion="约定赔偿上限（如合同总金额或已付价款），排除间接损失、利润损失与可得利益。",
        law_name="中华人民共和国民法典",
        article_no="第五百八十四条",
        false_positive_boundary=(
            "仅当出现无限赔偿、不设赔偿上限、包括间接损失/利润损失等表述时命中；"
            "约定“赔偿责任以合同总金额为限”的条款不命中。"
        ),
        detectors=(
            _p(r"(?:无限|不设上限|无上限)[^。；\n]{0,15}(?:赔偿|责任)"),
            _p(r"(?:赔偿|责任)[^。；\n]{0,20}(?:无限|不设上限|无上限)"),
            _p(r"(?:包括|包含)[^。；\n]{0,20}(?:间接损失|利润损失|可得利益)"),
        ),
        exclusions=(_p(r"(?:以合同总金额为限|以合同总额为限|赔偿限额|责任上限)"),),
    ),
    RuleSpec(
        rule_id="R-ACC-001",
        risk_type="acceptance_standard",
        clause_types=("delivery",),
        title="验收标准",
        condition="赋予一方以任何理由拒绝验收，或拒绝验收不承担违约后果，且未约定客观标准。",
        action="生成验收风险 finding：要求写明验收标准、期限与逾期视为通过。",
        risk_level="medium",
        issue="验收标准主观且缺少时限，可能导致交付方长期无法回款。",
        suggestion="写明验收标准、验收期限与逾期未反馈视为通过；拒绝验收须给出书面理由。",
        law_name="中华人民共和国民法典",
        article_no="第五百一十一条",
        false_positive_boundary=(
            "仅当出现任何理由拒绝验收、拒绝验收不视为违约、验收标准由单方确定等表述时命中；"
            "按附件技术标准验收、约定逾期未反馈视为通过的条款不命中。"
        ),
        detectors=_ACCEPTANCE_DETECTORS_DEFAULT,
        exclusions=(
            _p(r"(?:按|按照)[^。；\n]{0,20}(?:标准|约定)[^。；\n]{0,10}验收"),
            _p(r"逾期未反馈视为验收通过"),
        ),
        stance_detectors={
            "甲方": _ACCEPTANCE_DETECTORS_DEFAULT,
            "乙方": _acceptance_detectors(_A_PARTY),
            "neutral": _ACCEPTANCE_DETECTORS_DEFAULT,
        },
    ),
)

RULES_BY_ID = {rule.rule_id: rule for rule in RULES}

RULE_COVERAGE = {
    "付款与交付顺序": "R-PAY-001",
    "逾期付款免责": "R-PAY-002",
    "违约金上限": "R-PEN-001",
    "解除条件": "R-TERM-001",
    "保密": "R-CONF-001",
    "不可抗力": "R-FM-001",
    "管辖": "R-JUR-001",
    "责任限额": "R-LIAB-001",
    "验收标准": "R-ACC-001",
}


def _sentences(text: str) -> list[str]:
    return [segment.strip() for segment in SENTENCE_SPLIT.split(text) if segment.strip()]


def _patterns_for(rule: RuleSpec, stance: str) -> tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]]:
    detectors = rule.stance_detectors.get(stance, rule.detectors)
    exclusions = rule.stance_exclusions.get(stance, rule.exclusions)
    return detectors, exclusions


def detect_rules(text: str, clause_type: str, stance: str = "甲方") -> list[RuleMatch]:
    """按规则表逐句判定；同一规则在一条条款内只返回一次命中。

    stance 只影响方向类规则（付款义务、单方验收标准、单方管辖）；
    违约金上限在条款级排除，只要条款写明累计上限，整条不再报超限风险。
    """
    normalized = normalize_stance(stance)
    matches: list[RuleMatch] = []
    sentences = _sentences(text)
    for rule in RULES:
        if clause_type not in rule.clause_types:
            continue
        if any(pattern.search(text) for pattern in rule.clause_exclusions):
            continue
        detectors, exclusions = _patterns_for(rule, normalized)
        for sentence in sentences:
            if any(pattern.search(sentence) for pattern in exclusions):
                continue
            if any(pattern.search(sentence) for pattern in detectors):
                matches.append(RuleMatch(rule=rule, quote=sentence[:200]))
                break
    return matches


def rule_table_markdown() -> str:
    """输出规则表，供 README/汇报引用；内容与 RULES 同源。"""
    lines = [
        "| 规则 ID | 风险类型 | 条款类型 | 判定条件 | 命中后动作 | 法条依据 | 误报边界 |",
        "|---|---|---|---|---|---|---|",
    ]
    for rule in RULES:
        lines.append(
            "| {id} | {risk} | {types} | {cond} | {action} | {law} {article} | {boundary} |".format(
                id=rule.rule_id,
                risk=rule.risk_type,
                types="、".join(rule.clause_types),
                cond=rule.condition,
                action=rule.action,
                law=rule.law_name,
                article=rule.article_no,
                boundary=rule.false_positive_boundary,
            )
        )
    return "\n".join(lines)
