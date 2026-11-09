"""Streamlit 单页演示：上传、审查、确认/驳回、下载 docx。

只做单页，不引入前端框架；engine=mock 时用醒目横幅，避免把规则输出误当成真实模型效果。
"""

from __future__ import annotations

import os

import requests
import streamlit as st

API = os.getenv("LEXFLOW_API_URL", "http://127.0.0.1:8000")
RISK_ORDER = {"high": 0, "medium": 1, "low": 2}

st.set_page_config(page_title="lexflow M0", layout="wide")
st.title("lexflow M0：合同审查最小闭环")
st.caption("上传合同 → 选择立场 → 审查 → 人工确认/驳回 → 导出 docx。")


def _review(matter: dict, action: str, comment: str) -> None:
    response = requests.post(
        f"{API}/review/{matter['matter_id']}",
        json={"action": action, "comment": comment},
        timeout=600,
    )
    if response.status_code != 200:
        st.error(f"复核失败: {response.status_code} {response.text}")
        return
    payload = response.json()
    matter["status"] = payload["status"]
    matter["docx_path"] = payload.get("docx_path")
    matter["metrics"] = payload.get("metrics", matter.get("metrics", {}))
    st.session_state["matter"] = matter


with st.form("analyze"):
    file = st.file_uploader("上传合同（PDF/docx）", type=["pdf", "docx"])
    stance = st.selectbox("审查立场", ["甲方", "乙方"])
    as_of = st.text_input("法律时点 as_of", value="2026-11-03")
    engine = st.selectbox("engine", ["auto", "mock", "api"])
    submitted = st.form_submit_button("开始审查")

if submitted and file:
    files = {"file": (file.name, file.getvalue())}
    data = {"stance": stance, "as_of": as_of, "engine": engine}
    with st.spinner("审查中..."):
        response = requests.post(f"{API}/analyze", files=files, data=data, timeout=600)
    if response.status_code != 200:
        st.error(f"失败: {response.status_code} {response.text}")
    else:
        st.session_state["matter"] = response.json()

matter = st.session_state.get("matter")
if matter:
    engine_used = matter.get("engine", "unknown")
    if engine_used == "mock":
        st.error(
            "⚠ engine=mock：以下风险由固定规则表生成，仅用于链路/界面演示，"
            "不代表真实模型效果，也不构成法律意见。"
        )
    else:
        st.info(f"engine={engine_used}：以下为模型输出，仍需人工复核。")

    report = matter.get("report") or {}
    metrics = report.get("metrics") or matter.get("metrics") or {}
    node_ms = metrics.get("node_latency_ms") or {}
    st.write(
        f"matter_id: `{matter['matter_id']}`；status: `{matter['status']}`；"
        f"doc_id: `{report.get('doc_id') or 'n/a'}`"
    )
    cols = st.columns(5)
    cols[0].metric("LLM 调用", metrics.get("llm_calls", 0))
    cols[1].metric("prompt tokens", metrics.get("prompt_tokens", 0))
    cols[2].metric("completion tokens", metrics.get("completion_tokens", 0))
    cols[3].metric("节点耗时合计", f"{sum(node_ms.values())} ms")
    cols[4].metric("端到端耗时", f"{matter.get('elapsed_ms', 0)} ms")
    if node_ms:
        st.caption(
            "节点耗时："
            + "；".join(f"{name}={value}ms" for name, value in node_ms.items())
        )

    findings = report.get("findings", [])
    rows = [
        {
            "条款": item.get("clause_id"),
            "风险等级": item.get("risk_level"),
            "审查来源": " + ".join(item.get("reviewers") or [item.get("reviewer") or ""]).strip(" +"),
            "规则 ID": item.get("rule_id") or "无",
            "问题": item.get("issue"),
            "法条依据": " ".join(
                part for part in (item.get("law_name"), item.get("article_no")) if part
            )
            or f"未指定：{item.get('citation_reason') or '原因未提供'}",
            "定位锚": " · ".join(
                part for part in (item.get("doc_id"), item.get("locator")) if part
            )
            or "无定位",
            "原文": item.get("original_quote"),
            "建议": item.get("suggestion"),
        }
        for item in sorted(
            findings,
            key=lambda x: (RISK_ORDER.get(x.get("risk_level"), 9), x.get("clause_id", "")),
        )
    ]
    st.subheader(f"风险条款（{len(rows)} 条，按风险等级排序）")
    st.dataframe(rows, use_container_width=True, hide_index=True)

    if matter["status"] == "awaiting_review":
        comment = st.text_input("复核意见（可选）")
        left, right = st.columns(2)
        if left.button("确认并导出 docx", type="primary"):
            _review(matter, "approve", comment)
        if right.button("驳回（不导出）"):
            _review(matter, "reject", comment)
    elif matter["status"] == "completed":
        st.success("已确认并导出。")
        st.link_button("下载 docx", f"{API}/export/{matter['matter_id']}")
    elif matter["status"] == "rejected":
        st.warning("已驳回；checkpoint 保留，可用 service.review 重新确认。")
        st.link_button("查看当前 docx（若已生成）", f"{API}/export/{matter['matter_id']}")
