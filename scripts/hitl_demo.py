"""HITL 中断/恢复演示：需要执行两次独立进程。

第一次：python scripts/hitl_demo.py start
  - 用 require_confirmation=True 触发 interrupt，状态停在 awaiting_review；
  - 把 matter_id 写入 artifacts/hitl_demo_state.json。

第二次：python scripts/hitl_demo.py resume
  - 全新进程、全新 AnalyzeService，从同一个 SqliteSaver checkpoint 恢复；
  - 用 Command(resume=...)、继续跑完导出 docx，并打印状态与产物。

两次执行都输出 JSON，便于直接贴进汇报。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lexflow.service import AnalyzeService  # noqa: E402

STATE_FILE = ROOT / "artifacts" / "hitl_demo_state.json"


def _emit(stage: str, payload: dict) -> None:
    print(json.dumps({"stage": stage, "pid": os.getpid(), **payload}, ensure_ascii=False, indent=2))


def _start(args: argparse.Namespace) -> int:
    service = AnalyzeService()
    result = service.analyze(
        args.file,
        stance=args.stance,
        as_of=args.as_of,
        engine=args.engine,
        require_confirmation=True,
        auto_approve=False,
    )
    state = {
        "matter_id": result.matter_id,
        "file": str(Path(args.file).resolve()),
        "stance": args.stance,
        "as_of": args.as_of,
        "engine": result.engine,
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit(
        "start",
        {
            "matter_id": result.matter_id,
            "status": result.status,
            "engine": result.engine,
            "findings_count": result.findings_count,
            "elapsed_ms": result.elapsed_ms,
            "has_interrupt": result.status == "awaiting_review",
            "docx_path": result.docx_path,
            "state_file": str(STATE_FILE),
        },
    )
    return 0 if result.status == "awaiting_review" else 1


def _resume(args: argparse.Namespace) -> int:
    matter_id = args.matter_id
    if not matter_id:
        if not STATE_FILE.exists():
            print(f"缺少 state 文件: {STATE_FILE}；请先运行 start", file=sys.stderr)
            return 2
        matter_id = json.loads(STATE_FILE.read_text(encoding="utf-8"))["matter_id"]
    # 故意新建 service，证明恢复不依赖第一次进程的内存状态。
    service = AnalyzeService()
    result = service.review(matter_id, action=args.action, comment=args.comment)
    docx = Path(result.docx_path) if result.docx_path else None
    _emit(
        "resume",
        {
            "matter_id": result.matter_id,
            "status": result.status,
            "engine": result.engine,
            "findings_count": result.findings_count,
            "elapsed_ms": result.elapsed_ms,
            "docx_path": result.docx_path,
            "docx_exists": bool(docx and docx.exists()),
            "docx_bytes": docx.stat().st_size if docx and docx.exists() else 0,
            "new_process": True,
        },
    )
    return 0 if result.status in {"completed", "rejected"} else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HITL 中断/恢复演示")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="触发 interrupt 并保留 checkpoint")
    start.add_argument("--file", default=str(ROOT / "examples" / "sample_procurement_contract.pdf"))
    start.add_argument("--stance", default="甲方")
    start.add_argument("--as-of", default="2026-11-03")
    start.add_argument("--engine", default="mock")

    resume = sub.add_parser("resume", help="新进程恢复 checkpoint 并导出")
    resume.add_argument("--matter-id", default="")
    resume.add_argument("--action", default="approve", choices=["approve", "modify", "reject"])
    resume.add_argument("--comment", default="HITL demo: 新进程恢复后确认")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "start":
        return _start(args)
    if args.command == "resume":
        return _resume(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
