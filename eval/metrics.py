"""评测共用口径：严格溯源、引用分列、双路互补、三态闸门。

这些函数只做文本比对与计数，不调用模型、不读数据库。``run_eval.py``、
``run_synthetic_eval.py`` 与测试共用同一份实现，避免「同一句话在不同脚本里
算法不同」——README 里写的每个比率都必须能由这里的函数复算。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable, Mapping

PUNCT_RE = re.compile(r"\s+|[，。；：、,.!?;:\"'“”‘’（）()《》【】\[\]…—\-]")

GATES = ("G1", "G2", "G3", "G4", "G5")


def normalize(text: str) -> str:
    return PUNCT_RE.sub("", text or "")


def strict_traceable(finding: Mapping[str, Any], clause_text: Mapping[str, str]) -> bool:
    """严格溯源：original_quote 归一化后必须是所属条款正文的子串。

    比「clause_id 能对上 + 引文非空」更严：后者只证明条款存在，不证明引文真实。
    """
    quote = normalize(str(finding.get("original_quote") or ""))
    source = clause_text.get(str(finding.get("clause_id") or ""), "")
    return bool(quote) and quote in normalize(source)


def citation_status(finding: Mapping[str, Any]) -> str:
    return "cited" if finding.get("law_name") and finding.get("article_no") else "uncited"


def gate_status(verdict: Mapping[str, Any]) -> str:
    """兼容没有 status 字段的旧产物：pass / fail 由 passed 反推。"""
    status = verdict.get("status")
    if status in ("pass", "fail", "skipped"):
        return str(status)
    return "pass" if verdict.get("passed") else "fail"


def gates_summary(
    findings: Iterable[Mapping[str, Any]], verifications: Iterable[Mapping[str, Any]]
) -> dict[str, int]:
    """三态闸门统计。``all_pass`` 要求五道闸都是 pass；skipped 不算通过。"""
    findings = list(findings)
    by_finding: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for verdict in verifications:
        by_finding[str(verdict.get("finding_id", ""))].append(verdict)

    def statuses(finding: Mapping[str, Any]) -> list[str]:
        return [gate_status(item) for item in by_finding.get(str(finding.get("finding_id", "")), [])]

    def all_pass(finding: Mapping[str, Any]) -> bool:
        values = statuses(finding)
        return len(values) == len(GATES) and all(value == "pass" for value in values)

    cited = [item for item in findings if citation_status(item) == "cited"]
    uncited = [item for item in findings if citation_status(item) == "uncited"]
    skipped = sum(
        1
        for finding in findings
        if any(value == "skipped" for value in statuses(finding))
    )
    return {
        "all_pass": sum(1 for finding in findings if all_pass(finding)),
        "all_pass_cited": sum(1 for finding in cited if all_pass(finding)),
        "all_pass_uncited": sum(1 for finding in uncited if all_pass(finding)),
        "cited": len(cited),
        "uncited": len(uncited),
        "skipped_findings": skipped,
    }


def dual_review_stats(findings: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """双路互补：按（条款 + 归一化原文）聚成风险点，再看来源。

    - ``risk_points``：去重后的风险点数量；
    - ``merged_both``：两路都报到的风险点；
    - ``legal_only`` / ``commercial_only``：只有一路报到的风险点；
    - ``duplicates``：原始 findings 与风险点的差，即重复条目数。
    """
    findings = list(findings)
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for finding in findings:
        key = (
            str(finding.get("clause_id") or ""),
            normalize(str(finding.get("original_quote") or "")),
        )
        groups[key].append(finding)

    both = legal_only = commercial_only = 0
    for items in groups.values():
        reviewers: set[str] = set()
        for item in items:
            names = item.get("reviewers") or [item.get("reviewer")]
            reviewers.update(str(name) for name in names if name)
        if "legal" in reviewers and "commercial" in reviewers:
            both += 1
        elif "commercial" in reviewers:
            commercial_only += 1
        elif "legal" in reviewers:
            legal_only += 1
    return {
        "raw_findings": len(findings),
        "risk_points": len(groups),
        "merged_both": both,
        "legal_only": legal_only,
        "commercial_only": commercial_only,
        "duplicates": len(findings) - len(groups),
    }
