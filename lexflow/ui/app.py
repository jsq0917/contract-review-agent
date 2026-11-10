"""Streamlit 单页演示：上传、审查、确认/驳回、下载 docx。

只做单页，不引入前端框架；engine=mock 时用醒目横幅，避免把规则输出误当成真实模型效果。
"""

from __future__ import annotations

import os
import time
from datetime import date

import requests
import streamlit as st

API = os.getenv("LEXFLOW_API_URL", "http://127.0.0.1:8000")
ACTIVE_STATUS = {"queued", "running", "cancelling"}
RISK_ORDER = {"high": 0, "medium": 1, "low": 2}
STATUS_LABEL = {"pass": "通过", "fail": "不通过", "skipped": "未判定"}
STATUS_ICON = {"pass": "✓", "fail": "✗", "skipped": "–"}


def _gate_status(verdict: dict) -> str:
    status = verdict.get("status")
    if status in STATUS_LABEL:
        return status
    return "pass" if verdict.get("passed") else "fail"

st.set_page_config(page_title="lexflow M0", layout="wide")
st.title("lexflow M0：合同审查最小闭环")
st.caption("上传合同 → 选择立场 → 审查 → 人工确认/驳回 → 导出 docx。")


def _headers(matter: dict) -> dict[str, str]:
    token = matter.get("token") or ""
    return {"X-Matter-Token": token} if token else {}


def _review(matter: dict, action: str, comment: str) -> None:
    try:
        response = requests.post(
            f"{API}/review/{matter['matter_id']}",
            json={"action": action, "comment": comment},
            headers=_headers(matter),
            timeout=600,
        )
    except requests.RequestException as exc:
        st.error(f"连不上后端 API（{API}）：{exc}。请确认 `uv run lexflow-api` 已启动。")
        return
    if response.status_code != 200:
        st.error(f"复核失败: {response.status_code} {response.text}")
        return
    payload = response.json()
    matter["status"] = payload["status"]
    matter["docx_path"] = payload.get("docx_path")
    matter["metrics"] = payload.get("metrics", matter.get("metrics", {}))
    st.session_state["matter"] = matter


@st.cache_data(ttl=30, show_spinner=False)
def _health() -> dict:
    """取后端检索/精排状态做开关默认值与可用性提示；后端没起时返回空字典。"""
    try:
        return requests.get(f"{API}/health", timeout=5).json()
    except Exception:
        return {}


health = _health()


def _rerank_hint() -> None:
    """开关下面的说明：把「能不能开」和「开了要付多少代价」说清楚。"""
    if not health:
        st.caption("读不到 /health：开关按默认关闭处理，开启后是否生效以报告里的 rerank_used 为准。")
        return
    if "reranker_model_found" not in health:
        st.caption("后端未返回精排状态（可能是旧版本）：开关仍可切换，是否生效以报告里的 rerank_used 为准。")
        return
    if not health.get("reranker_model_found"):
        st.caption(
            "未找到本地 reranker 模型：开关可以打开，但本次会自动降级为 hybrid 检索，"
            "报告里会标注 rerank_used=false。"
        )
        return
    warmup = health.get("reranker_warmup")
    if warmup == "running":
        st.caption("精排模型正在后台预热（约 20s）：就绪前首次开启会等一会儿，之后每条款约 0.25s。")
        return
    if warmup == "failed":
        st.caption(
            f"精排模型预热失败（{health.get('reranker_reason') or '原因未记录'}）："
            "开关仍可打开，但会降级为 hybrid。"
        )
        return
    device = health.get("reranker_device") or "首次启用时加载"
    tail = "；模型已预热，随开随用。" if warmup == "ready" else "；首次启用需加载模型约 10–20s。"
    st.caption(
        f"bge-reranker-v2-m3 · {device}：每条款多一次精排（CPU 约 1.5s/次，GPU 约 0.25s/次），"
        "法务/商务两路共用同一次打分" + tail
    )


with st.form("analyze"):
    file = st.file_uploader("上传合同（PDF/docx）", type=["pdf", "docx"])
    stance = st.selectbox("审查立场", ["甲方", "乙方"])
    as_of = st.text_input("法律时点 as_of", value=date.today().isoformat())
    engine = st.selectbox(
        "engine",
        ["mock", "auto", "api"],
        help="默认 mock：不调用模型、零成本，适合演示链路；auto 有 API key 时走真实模型，单份约 70–160s。",
    )
    # 用 key + session_state 存用户选择：否则每次 rerun 都会按 value= 重新求值，
    # 用户刚打开的开关可能在提交前被默认值覆盖回去。
    if "rerank_enabled" not in st.session_state:
        st.session_state["rerank_enabled"] = bool(health.get("reranker_configured", False))
    rerank = st.toggle(
        "启用 rerank 精排",
        key="rerank_enabled",
        help="开启后先 hybrid 召回候选，再用 bge-reranker 重排；找不到合适法条的情况会更少，但耗时增加。",
    )
    _rerank_hint()
    submitted = st.form_submit_button("开始审查")

if submitted and file:
    files = {"file": (file.name, file.getvalue())}
    data = {
        "stance": stance,
        "as_of": as_of,
        "engine": engine,
        "rerank": "true" if rerank else "false",
    }
    with st.spinner("提交任务..."):
        try:
            response = requests.post(f"{API}/analyze", files=files, data=data, timeout=600)
        except requests.RequestException as exc:
            st.error(
                f"连不上后端 API（{API}）：{exc}。\n\n"
                "先启动 `uv run lexflow-api`，或设置 `LEXFLOW_API_URL` 指向已运行的服务。"
            )
            st.stop()
    if response.status_code not in (200, 202):
        st.error(f"失败: {response.status_code} {response.text}")
    else:
        record = response.json()
        headers = _headers(record)
        deadline = time.time() + 600
        payload = record
        with st.spinner(f"审查中（task {record['task_id']}）..."):
            while payload.get("status") in ACTIVE_STATUS and time.time() < deadline:
                time.sleep(0.5)
                try:
                    status_response = requests.get(
                        f"{API}/tasks/{record['task_id']}", headers=headers, timeout=10
                    )
                    if status_response.status_code == 200:
                        payload = status_response.json()
                except requests.RequestException:
                    break
        result = payload.get("result") or {}
        matter = {
            "task_id": record["task_id"],
            "matter_id": record["matter_id"],
            "token": record.get("token", ""),
            "status": payload.get("status", record.get("status")),
            "error": payload.get("error"),
            **result,
        }
        if payload.get("error"):
            st.error(
                f"任务失败（{payload['error'].get('code')}）：{payload['error'].get('message')}"
            )
        st.session_state["matter"] = matter

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

    rerank_requested = metrics.get("rerank_requested")
    if rerank_requested and not metrics.get("rerank_used"):
        st.warning(
            "已请求精排但未生效，本次实际走 hybrid 检索："
            f"{metrics.get('rerank_reason') or '原因未记录'}"
        )
    elif rerank_requested:
        st.caption(
            f"精排已生效：{metrics.get('rerank_applied', 0)} 次重排、"
            f"{metrics.get('rerank_cache_hits', 0)} 次命中缓存、"
            f"{metrics.get('rerank_fallback', 0)} 次降级。"
        )
    elif rerank_requested is False:
        st.caption("本次未启用精排（hybrid 检索）。")

    findings = report.get("findings", [])
    verifications = report.get("verifications", [])
    by_finding: dict[str, list[dict]] = {}
    for verdict in verifications:
        by_finding.setdefault(str(verdict.get("finding_id", "")), []).append(verdict)

    def _gate_brief(finding_id: str) -> str:
        items = sorted(by_finding.get(finding_id, []), key=lambda x: str(x.get("gate", "")))
        if not items:
            return "未核验"
        return " ".join(
            f"{item.get('gate')}{STATUS_ICON[_gate_status(item)]}" for item in items
        )

    def _all_pass(finding_id: str) -> bool:
        items = by_finding.get(finding_id, [])
        return len(items) == 5 and all(_gate_status(item) == "pass" for item in items)

    cited = sum(
        1 for item in findings if item.get("law_name") and item.get("article_no")
    )
    gates_ok = sum(1 for item in findings if _all_pass(str(item.get("finding_id", ""))))
    st.caption(
        f"五闸全过 {gates_ok}/{len(findings)}（skipped 不计通过）；"
        f"引用 {cited} 条、未引用 {len(findings) - cited} 条。"
        "API 引擎没有 rule_id，G4 只确认存在引文、不校验法条适用性，显示为「未判定」。"
    )

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
            "五闸": _gate_brief(str(item.get("finding_id", ""))),
            "引文": item.get("cited_quote") or "—",
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

    if verifications:
        with st.expander("核验详情（G1–G5 逐条）"):
            st.dataframe(
                [
                    {
                        "finding_id": item.get("finding_id"),
                        "闸门": item.get("gate"),
                        "结果": STATUS_LABEL[_gate_status(item)],
                        "说明": item.get("detail"),
                    }
                    for item in verifications
                ],
                use_container_width=True,
                hide_index=True,
            )

    if matter["status"] == "awaiting_review":
        comment = st.text_input("复核意见（可选）")
        left, right = st.columns(2)
        if left.button("确认并导出 docx", type="primary"):
            _review(matter, "approve", comment)
        if right.button("驳回（不导出）"):
            _review(matter, "reject", comment)
    elif matter["status"] == "completed":
        st.success("已确认并导出。")
        st.link_button(
            "下载 docx",
            f"{API}/export/{matter['matter_id']}?token={matter.get('token', '')}",
        )
    elif matter["status"] == "rejected":
        st.warning("已驳回；checkpoint 保留，可用 service.review 重新确认。")
        st.link_button(
            "查看当前 docx（若已生成）",
            f"{API}/export/{matter['matter_id']}?token={matter.get('token', '')}",
        )
    elif matter["status"] in ("cancelled", "failed", "interrupted"):
        st.warning(f"任务状态：{matter['status']}；不提供复核与导出。")
