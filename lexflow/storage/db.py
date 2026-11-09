"""SQLite 单库：业务表与 LangGraph checkpoint 使用同一数据库文件、不同表。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lexflow.models import Clause, Evidence, Finding, Report, Verification


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS matters (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                filename TEXT NOT NULL,
                engine TEXT NOT NULL,
                status TEXT NOT NULL,
                stance TEXT NOT NULL,
                as_of TEXT NOT NULL,
                result_json TEXT,
                docx_path TEXT
            );
            CREATE TABLE IF NOT EXISTS clauses (
                matter_id TEXT NOT NULL,
                clause_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                text TEXT NOT NULL,
                page_no INTEGER,
                bbox_json TEXT,
                char_start INTEGER NOT NULL,
                char_end INTEGER NOT NULL,
                clause_type TEXT NOT NULL,
                doc_id TEXT NOT NULL DEFAULT '',
                locator TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (matter_id, clause_id)
            );
            CREATE TABLE IF NOT EXISTS findings (
                matter_id TEXT NOT NULL,
                finding_id TEXT NOT NULL,
                clause_id TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                issue TEXT NOT NULL,
                suggestion TEXT NOT NULL,
                original_quote TEXT NOT NULL,
                cited_quote TEXT,
                law_name TEXT,
                article_no TEXT,
                evidence_id TEXT,
                rule_id TEXT,
                risk_type TEXT NOT NULL DEFAULT '',
                reviewers TEXT NOT NULL DEFAULT '[]',
                citation_reason TEXT,
                engine TEXT NOT NULL,
                doc_id TEXT NOT NULL DEFAULT '',
                locator TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (matter_id, finding_id)
            );
            CREATE TABLE IF NOT EXISTS evidence (
                matter_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                law_name TEXT NOT NULL,
                article_no TEXT NOT NULL,
                quote TEXT NOT NULL,
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                source_url TEXT NOT NULL,
                version_id TEXT NOT NULL,
                score REAL NOT NULL,
                PRIMARY KEY (matter_id, evidence_id)
            );
            CREATE TABLE IF NOT EXISTS verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                matter_id TEXT NOT NULL,
                finding_id TEXT NOT NULL,
                gate TEXT NOT NULL,
                passed INTEGER NOT NULL,
                detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS human_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                matter_id TEXT NOT NULL,
                action TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,
                decision_json TEXT
            );
            """
        )
        self._migrate_findings_pk()

        # 兼容阶段 1/2 已有的 findings 表：新增列。
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(findings)")}
        if "rule_id" not in columns:
            self.conn.execute("ALTER TABLE findings ADD COLUMN rule_id TEXT")
        if "doc_id" not in columns:
            self.conn.execute("ALTER TABLE findings ADD COLUMN doc_id TEXT NOT NULL DEFAULT ''")
        if "locator" not in columns:
            self.conn.execute("ALTER TABLE findings ADD COLUMN locator TEXT NOT NULL DEFAULT ''")
        if "risk_type" not in columns:
            self.conn.execute("ALTER TABLE findings ADD COLUMN risk_type TEXT NOT NULL DEFAULT ''")
        if "reviewers" not in columns:
            self.conn.execute("ALTER TABLE findings ADD COLUMN reviewers TEXT NOT NULL DEFAULT '[]'")
        if "citation_reason" not in columns:
            self.conn.execute("ALTER TABLE findings ADD COLUMN citation_reason TEXT")

        # 阶段 4：clauses 需要 locator/doc_id，且 docx 的 page_no 必须为 NULL。
        clause_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(clauses)")}
        if "locator" not in clause_columns or "doc_id" not in clause_columns:
            self.conn.executescript(
                """
                ALTER TABLE clauses RENAME TO clauses_legacy;
                CREATE TABLE clauses (
                    matter_id TEXT NOT NULL,
                    clause_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    page_no INTEGER,
                    bbox_json TEXT,
                    char_start INTEGER NOT NULL,
                    char_end INTEGER NOT NULL,
                    clause_type TEXT NOT NULL,
                    doc_id TEXT NOT NULL DEFAULT '',
                    locator TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (matter_id, clause_id)
                );
                INSERT INTO clauses
                    (matter_id, clause_id, idx, text, page_no, bbox_json,
                     char_start, char_end, clause_type, doc_id, locator)
                SELECT matter_id, clause_id, idx, text, page_no, bbox_json,
                       char_start, char_end, clause_type, '', ''
                FROM clauses_legacy;
                DROP TABLE clauses_legacy;
                """
            )
        self.conn.commit()

    def save_matter(self, matter_id: str, filename: str, engine: str, stance: str, as_of: str) -> None:
        stamp = now_iso()
        self.conn.execute(
            "INSERT OR REPLACE INTO matters (id, created_at, updated_at, filename, engine, status, stance, as_of) "
            "VALUES (?, COALESCE((SELECT created_at FROM matters WHERE id=?), ?), ?, ?, ?, 'created', ?, ?)",
            (matter_id, matter_id, stamp, stamp, filename, engine, stance, as_of),
        )
        self.conn.commit()

    def update_matter(
        self,
        matter_id: str,
        *,
        status: str | None = None,
        result: Report | dict[str, Any] | None = None,
        docx_path: str | None = None,
    ) -> None:
        fields: list[str] = ["updated_at = ?"]
        values: list[Any] = [now_iso()]
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if result is not None:
            fields.append("result_json = ?")
            if isinstance(result, Report):
                values.append(result.model_dump_json())
            else:
                values.append(json.dumps(result, ensure_ascii=False, default=str))
        if docx_path is not None:
            fields.append("docx_path = ?")
            values.append(docx_path)
        values.append(matter_id)
        self.conn.execute(f"UPDATE matters SET {', '.join(fields)} WHERE id = ?", values)
        self.conn.commit()

    def _migrate_findings_pk(self) -> None:
        """把旧库的单列主键 finding_id 迁移为 (matter_id, finding_id)，保数据、可重复执行。"""
        info = self.conn.execute("PRAGMA table_info(findings)").fetchall()
        pk_cols = [row["name"] for row in info if row["pk"]]
        if pk_cols == ["matter_id", "finding_id"]:
            return
        self.conn.executescript(
            """
            BEGIN;
            DROP TABLE IF EXISTS findings_legacy;
            ALTER TABLE findings RENAME TO findings_legacy;
            CREATE TABLE findings (
                matter_id TEXT NOT NULL,
                finding_id TEXT NOT NULL,
                clause_id TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                issue TEXT NOT NULL,
                suggestion TEXT NOT NULL,
                original_quote TEXT NOT NULL,
                cited_quote TEXT,
                law_name TEXT,
                article_no TEXT,
                evidence_id TEXT,
                rule_id TEXT,
                risk_type TEXT NOT NULL DEFAULT '',
                reviewers TEXT NOT NULL DEFAULT '[]',
                citation_reason TEXT,
                engine TEXT NOT NULL,
                doc_id TEXT NOT NULL DEFAULT '',
                locator TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (matter_id, finding_id)
            );
            INSERT INTO findings (
                matter_id, finding_id, clause_id, reviewer, risk_level, issue, suggestion,
                original_quote, cited_quote, law_name, article_no, evidence_id, rule_id,
                engine, doc_id, locator
            )
            SELECT
                matter_id, finding_id, clause_id, reviewer, risk_level, issue, suggestion,
                original_quote, cited_quote, law_name, article_no, evidence_id, rule_id,
                engine, doc_id, locator
            FROM findings_legacy;
            DROP TABLE findings_legacy;
            COMMIT;
            """
        )

    def replace_clauses(self, matter_id: str, clauses: list[Clause]) -> None:
        self.conn.execute("DELETE FROM clauses WHERE matter_id = ?", (matter_id,))
        for clause in clauses:
            self.conn.execute(
                "INSERT INTO clauses "
                "(matter_id, clause_id, idx, text, page_no, bbox_json, char_start, char_end, "
                "clause_type, doc_id, locator) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    matter_id,
                    clause.clause_id,
                    clause.index,
                    clause.text,
                    clause.page_no,
                    clause.bbox.model_dump_json() if clause.bbox else None,
                    clause.char_start,
                    clause.char_end,
                    clause.clause_type,
                    clause.doc_id,
                    clause.locator,
                ),
            )
        self.conn.commit()

    def replace_findings(self, matter_id: str, findings: list[Finding]) -> None:
        self.conn.execute("DELETE FROM findings WHERE matter_id = ?", (matter_id,))
        for finding in findings:
            self.conn.execute(
                "INSERT OR REPLACE INTO findings "
                "(finding_id, matter_id, clause_id, reviewer, risk_level, issue, suggestion, original_quote, "
                "cited_quote, law_name, article_no, evidence_id, rule_id, risk_type, reviewers, "
                "citation_reason, engine, doc_id, locator) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    finding.finding_id,
                    matter_id,
                    finding.clause_id,
                    finding.reviewer,
                    finding.risk_level,
                    finding.issue,
                    finding.suggestion,
                    finding.original_quote,
                    finding.cited_quote,
                    finding.law_name,
                    finding.article_no,
                    finding.evidence_id,
                    finding.rule_id,
                    finding.risk_type,
                    json.dumps(finding.reviewers or [finding.reviewer], ensure_ascii=False),
                    finding.citation_reason,
                    finding.engine,
                    finding.doc_id,
                    finding.locator,
                ),
            )
        self.conn.commit()

    def replace_evidence(self, matter_id: str, evidence: list[Evidence]) -> None:
        self.conn.execute("DELETE FROM evidence WHERE matter_id = ?", (matter_id,))
        for item in evidence:
            self.conn.execute(
                "INSERT OR REPLACE INTO evidence "
                "(matter_id, evidence_id, law_name, article_no, quote, effective_from, effective_to, "
                "source_url, version_id, score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    matter_id,
                    item.evidence_id,
                    item.law_name,
                    item.article_no,
                    item.quote,
                    item.effective_from.isoformat(),
                    item.effective_to.isoformat() if item.effective_to else None,
                    item.source_url,
                    item.version_id,
                    item.score,
                ),
            )
        self.conn.commit()

    def replace_verifications(self, matter_id: str, verifications: list[Verification]) -> None:
        self.conn.execute("DELETE FROM verifications WHERE matter_id = ?", (matter_id,))
        for item in verifications:
            self.conn.execute(
                "INSERT INTO verifications (matter_id, finding_id, gate, passed, detail) VALUES (?, ?, ?, ?, ?)",
                (matter_id, item.finding_id, item.gate, int(item.passed), item.detail),
            )
        self.conn.commit()

    def add_human_review(self, matter_id: str, action: str, comment: str, decision: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO human_reviews (matter_id, action, comment, created_at, decision_json) VALUES (?, ?, ?, ?, ?)",
            (matter_id, action, comment, now_iso(), json.dumps(decision, ensure_ascii=False)),
        )
        self.conn.commit()

    def get_matter(self, matter_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM matters WHERE id = ?", (matter_id,)).fetchone()
        return dict(row) if row else None

    def count_findings(self, matter_id: str) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM findings WHERE matter_id = ?", (matter_id,)).fetchone()
        return int(row["n"]) if row else 0

    def list_findings(self, matter_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM findings WHERE matter_id = ? ORDER BY finding_id", (matter_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self.conn.close()
