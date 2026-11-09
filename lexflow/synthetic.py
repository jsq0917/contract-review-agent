"""可复现的合成合同生成器。

性质：**合成数据**，不是真实合同分布，也不是法律范本。
gold 由风险注入规则反推：哪一条被注入哪类风险，就把该条标为 gold；这不是律师标注。
因此后续指标只能说明「注入风险是否被检出」，不能外推到真实合同审查效果。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from docx import Document

GENERATOR_VERSION = "synthetic-v2"
DEFAULT_SEED = 20261103

CN_ARTICLES = [
    "第一条",
    "第二条",
    "第三条",
    "第四条",
    "第五条",
    "第六条",
    "第七条",
    "第八条",
    "第九条",
    "第十条",
    "第十一条",
    "第十二条",
]

ARTICLE_PREFIX_RE = re.compile(r"^(第[一二三四五六七八九十百千零]+条)")

DISCLAIMER = (
    "合成数据，非真实合同分布；gold 由注入规则反推，非律师标注；"
    "指标不可外推到真实合同审查效果。"
)


@dataclass(frozen=True)
class RiskSpec:
    """一处被注入的风险条款。"""

    article_no: str
    risk_type: str
    rule: str


@dataclass(frozen=True)
class SyntheticContract:
    """一份合成合同及其注入记录。"""

    file: str
    template_id: str
    template_name: str
    variant: int
    seed: int
    profile: str
    text: str
    risks: tuple[RiskSpec, ...]

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def article_no_of(text: str) -> str | None:
    """从条款文本行首取中文条号，供评测对齐 gold。"""
    match = ARTICLE_PREFIX_RE.match(text.strip())
    return match.group(1) if match else None


# 三种合同模板：只做结构差异，条款文本全部为自造样例。
TEMPLATES: dict[str, dict[str, str]] = {
    "tech": {
        "name": "技术服务合同",
        "party_a": "某科技有限公司",
        "party_b": "某咨询有限公司",
        "subject": "乙方为甲方提供合同流程咨询服务，具体交付物以附件一为准。",
        "delivery": (
            "乙方应在合同生效后三十日内提交交付物。"
            "甲方应在收到交付物后十个工作日内验收并书面反馈。"
        ),
        "confidentiality": (
            "双方应对履约过程中知悉的商业秘密承担保密义务，保密期限为合同终止后两年。"
        ),
        "notice": "双方通知应以书面形式送达合同载明的联系地址，送达即视为生效。",
    },
    "proc": {
        "name": "设备采购合同",
        "party_a": "某制造有限公司",
        "party_b": "某设备有限公司",
        "subject": "乙方向甲方供应生产线设备一套，型号与数量以附件清单为准。",
        "delivery": (
            "乙方应在收到预付款后一百二十日内交付。"
            "甲方应在到货后十个工作日内验收并书面反馈。"
        ),
        "confidentiality": "双方应对交易价格与设备参数承担保密义务，保密期限为合同终止后三年。",
        "notice": "双方通知应以书面形式送达合同载明的联系地址，送达即视为生效。",
    },
    "dev": {
        "name": "软件开发合同",
        "party_a": "某零售有限公司",
        "party_b": "某软件有限公司",
        "subject": "乙方为甲方开发合同管理软件，功能清单与里程碑以附件一为准。",
        "delivery": (
            "乙方应按里程碑提交可运行版本。"
            "甲方应在收到版本后十个工作日内验收并书面反馈。"
        ),
        "confidentiality": "双方应对源代码与业务数据承担保密义务，保密期限为合同终止后三年。",
        "notice": "双方通知应以书面形式送达合同载明的联系地址，送达即视为生效。",
    },
}

# v1 是阶段 1 的原始数据集（4 变体 / 8 条款），保留用于修改前后对照；
# v2 新增 4 类风险（保密、不可抗力、责任限额、验收标准），共 6 变体 / 12 条款。
PROFILE_PLANS: dict[str, tuple[tuple[str, ...], ...]] = {
    "v1": (
        ("payment",),
        ("liability",),
        ("termination", "payment"),
        ("jurisdiction", "liability"),
    ),
    "v2": (
        ("payment",),
        ("liability",),
        ("termination", "payment"),
        ("jurisdiction", "liability"),
        ("confidentiality_scope", "force_majeure"),
        ("liability_cap", "acceptance_standard"),
    ),
}

PROFILE_CLAUSE_ORDER: dict[str, tuple[str, ...]] = {
    "v1": (
        "subject",
        "payment",
        "delivery",
        "liability",
        "termination",
        "jurisdiction",
        "confidentiality",
        "notice",
    ),
    "v2": (
        "subject",
        "payment",
        "delivery",
        "liability",
        "termination",
        "jurisdiction",
        "confidentiality",
        "notice",
        "force_majeure",
        "liability_cap",
        "acceptance_standard",
        "confidentiality_scope",
    ),
}

CLAUSES_PER_PROFILE = {"v1": 8, "v2": 12}
CLAUSES_PER_CONTRACT = CLAUSES_PER_PROFILE["v2"]

RISK_RULES: dict[str, str] = {
    "payment": "要求合同签订后短期全额付款，且乙方尚未开始履行",
    "liability": "违约金按日千分之五且不设上限",
    "termination": "甲方可无理由单方解除且不承担赔偿责任",
    "jurisdiction": "约定由甲方所在地人民法院管辖",
    "confidentiality_scope": "要求永久保密且把已公开信息纳入保密范围，并排除资料返还",
    "force_majeure": "排除不可抗力免责，要求不可抗力下仍继续履行全部义务",
    "liability_cap": "承担无限赔偿责任并覆盖间接损失与利润损失",
    "acceptance_standard": "甲方可以任何理由拒绝验收且不视为违约",
}

# 风险版本：包含 mock 规则可命中的关键词（付款/违约金/单方解除/管辖）。
RISK_CLAUSES: dict[str, str] = {
    "payment": (
        "合同价款为人民币{amount}万元。"
        "甲方应在合同签订后五个工作日内付款，且付款比例为合同总价的百分之百，"
        "乙方尚未开始履行。"
    ),
    "liability": "任何一方违约的，应按合同总额每日千分之五支付违约金，违约金不设上限。",
    "termination": "甲方有权在任何时候无理由单方解除本合同，且无需承担任何赔偿责任。",
    "jurisdiction": "因本合同产生的争议，由甲方所在地人民法院管辖。",
    "confidentiality_scope": (
        "乙方对甲方披露的全部信息承担永久保密义务，保密范围包括已公开信息，"
        "合同终止后无需返还有关资料。"
    ),
    "force_majeure": (
        "任何一方不得以不可抗力为由主张免责，即使不可抗力持续超过九十日，"
        "仍应继续履行全部义务。"
    ),
    "liability_cap": (
        "乙方对甲方因本合同遭受的全部损失承担无限赔偿责任，包括间接损失与利润损失，"
        "不设赔偿上限。"
    ),
    "acceptance_standard": "甲方有权以任何理由拒绝验收，且拒绝验收不视为甲方违约。",
}

# 安全版本：刻意避开 mock 触发关键词，便于把「注入风险」与「普通条款」分开。
SAFE_CLAUSES: dict[str, str] = {
    "payment": "合同价款为人民币{amount}万元。双方按月对账，凭双方确认的对账单于次月十五日前结算。",
    "liability": "任何一方违约的，违约方应赔偿守约方因此遭受的直接损失。",
    "termination": "任何一方严重违约且经催告后三十日仍未纠正的，对方可以解除合同。",
    "jurisdiction": "双方应先行协商；协商不成的，提交合同签订地人民法院诉讼解决。",
    "confidentiality_scope": (
        "双方仅对履约过程中知悉的非公开商业秘密承担保密义务，保密期限为合同终止后三年，"
        "期满后返还或销毁保密资料。"
    ),
    "force_majeure": (
        "发生不可抗力导致无法履行的，受影响方应在十日内书面通知对方并提供证明，"
        "双方协商延期履行。"
    ),
    "liability_cap": (
        "任何一方违约的，违约方应赔偿守约方因此遭受的直接损失，"
        "赔偿责任以合同总金额为限。"
    ),
    "acceptance_standard": (
        "甲方应在收到交付物后十个工作日内按附件约定的技术标准验收并书面反馈；"
        "逾期未反馈视为验收通过。"
    ),
}

CLAUSE_ORDER = PROFILE_CLAUSE_ORDER["v2"]


def build_contract(
    template_id: str,
    variant: int,
    seed: int,
    profile: str = "v2",
) -> SyntheticContract:
    """按模板、变体与数据剖面生成一份合同；同一参数输出完全一致。"""
    if template_id not in TEMPLATES:
        raise KeyError(f"未知模板: {template_id}")
    if profile not in PROFILE_PLANS:
        raise KeyError(f"未知数据剖面: {profile}")
    plans = PROFILE_PLANS[profile]
    if not 0 <= variant < len(plans):
        raise KeyError(f"未知变体: {variant}")

    template = TEMPLATES[template_id]
    plan = set(plans[variant])
    clause_order = PROFILE_CLAUSE_ORDER[profile]
    # 整数种子，避免依赖字符串哈希实现细节。
    rng = random.Random(seed + variant * 37 + list(TEMPLATES).index(template_id) * 1009)
    amount = rng.choice([10, 20, 50, 100, 200])

    clause_bodies: list[str] = []
    risks: list[RiskSpec] = []
    for index, kind in enumerate(clause_order):
        article_no = CN_ARTICLES[index]
        if kind in RISK_CLAUSES:
            is_risk = kind in plan
            body = (RISK_CLAUSES if is_risk else SAFE_CLAUSES)[kind].format(amount=amount)
            if is_risk:
                risks.append(RiskSpec(article_no=article_no, risk_type=kind, rule=RISK_RULES[kind]))
        else:
            body = template[kind]
        clause_bodies.append(f"{article_no} {body}")

    header = [
        f"{template['name']}（合成样例）",
        "",
        f"合同编号：SYN-{template_id.upper()}-{variant:02d}",
        f"甲方：{template['party_a']}（合成主体）",
        f"乙方：{template['party_b']}（合成主体）",
        "",
    ]
    text = "\n".join(header + clause_bodies) + "\n"
    return SyntheticContract(
        file=f"contracts/{template_id}_{variant}.docx",
        template_id=template_id,
        template_name=template["name"],
        variant=variant,
        seed=seed,
        profile=profile,
        text=text,
        risks=tuple(risks),
    )


def _write_docx(path: Path, text: str) -> None:
    document = Document()
    for line in text.splitlines():
        if line.strip():
            document.add_paragraph(line.strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)


def generate_dataset(
    out_dir: str | Path,
    seed: int = DEFAULT_SEED,
    profile: str = "v2",
) -> list[SyntheticContract]:
    """生成全部合成合同、gold 与 manifest；清理旧的 docx，保证目录与 gold 一一对应。"""
    if profile not in PROFILE_PLANS:
        raise KeyError(f"未知数据剖面: {profile}")
    out_dir = Path(out_dir)
    contracts_dir = out_dir / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    for old in contracts_dir.glob("*.docx"):
        old.unlink()

    records: list[SyntheticContract] = []
    for template_id in TEMPLATES:
        for variant in range(len(PROFILE_PLANS[profile])):
            contract = build_contract(template_id, variant, seed, profile=profile)
            _write_docx(out_dir / contract.file, contract.text)
            records.append(contract)

    gold_path = out_dir / "gold.jsonl"
    with gold_path.open("w", encoding="utf-8", newline="\n") as handle:
        for contract in records:
            row = {
                "file": contract.file,
                "template": contract.template_id,
                "template_name": contract.template_name,
                "variant": contract.variant,
                "seed": contract.seed,
                "profile": contract.profile,
                "stance": "甲方",
                "as_of": "2026-11-03",
                "expected_clause_count": CLAUSES_PER_PROFILE[profile],
                "expected_risk_article_nos": [risk.article_no for risk in contract.risks],
                "risk_clauses": [
                    {
                        "article_no": risk.article_no,
                        "risk_type": risk.risk_type,
                        "rule": risk.rule,
                    }
                    for risk in contract.risks
                ],
                "text_sha256": contract.text_sha256,
                "note": DISCLAIMER,
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "profile": profile,
        "count": len(records),
        "clauses_per_contract": CLAUSES_PER_PROFILE[profile],
        "risk_injection_total": sum(len(record.risks) for record in records),
        "risk_types": sorted({risk.risk_type for record in records for risk in record.risks}),
        "nature": "合成数据，非真实合同分布",
        "gold_method": "由风险注入规则反推，非律师标注",
        "disclaimer": DISCLAIMER,
        "generated_files": [
            {"file": record.file, "text_sha256": record.text_sha256} for record in records
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return records
