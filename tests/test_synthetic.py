"""合成数据集的机械验收：可复现、gold 与合同一一对应、注入条号存在、端到端可跑。"""

from __future__ import annotations

import json
from pathlib import Path

from docx import Document

from lexflow.parse import parse_document
from lexflow.segment import segment_clauses
from lexflow.synthetic import (
    CLAUSES_PER_PROFILE,
    CN_ARTICLES,
    DEFAULT_SEED,
    article_no_of,
    generate_dataset,
)


def _read_gold(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_generate_dataset_is_reproducible(tmp_path: Path) -> None:
    first = generate_dataset(tmp_path / "a", seed=DEFAULT_SEED)
    second = generate_dataset(tmp_path / "b", seed=DEFAULT_SEED)

    assert len(first) == len(second) == 18
    assert [record.file for record in first] == [record.file for record in second]
    assert [record.text for record in first] == [record.text for record in second]
    assert [record.text_sha256 for record in first] == [record.text_sha256 for record in second]

    v1_first = generate_dataset(tmp_path / "v1a", seed=DEFAULT_SEED, profile="v1")
    v1_second = generate_dataset(tmp_path / "v1b", seed=DEFAULT_SEED, profile="v1")
    assert len(v1_first) == len(v1_second) == 12
    assert [record.text_sha256 for record in v1_first] == [
        record.text_sha256 for record in v1_second
    ]


def test_gold_corresponds_one_to_one_and_articles_exist(tmp_path: Path) -> None:
    out_dir = tmp_path / "dataset"
    records = generate_dataset(out_dir, seed=DEFAULT_SEED)
    gold = _read_gold(out_dir / "gold.jsonl")

    assert len(gold) == len(records) == 18
    gold_files = {row["file"] for row in gold}
    record_files = {record.file for record in records}
    assert gold_files == record_files
    assert len(gold_files) == len(gold)

    for row, record in zip(gold, records, strict=True):
        assert row["file"] == record.file
        assert row["text_sha256"] == record.text_sha256
        assert row["expected_clause_count"] == CLAUSES_PER_PROFILE["v2"]
        sample = out_dir / row["file"]
        assert sample.exists()
        paragraphs = [paragraph.text for paragraph in Document(sample).paragraphs]
        for article_no in row["expected_risk_article_nos"]:
            assert any(text.startswith(f"{article_no} ") for text in paragraphs), article_no


def test_segmentation_matches_gold(tmp_path: Path) -> None:
    out_dir = tmp_path / "dataset"
    generate_dataset(out_dir, seed=DEFAULT_SEED)
    gold = _read_gold(out_dir / "gold.jsonl")

    for row in gold:
        clauses = segment_clauses(parse_document(out_dir / row["file"]))
        assert len(clauses) == row["expected_clause_count"]
        numbers = [article_no_of(clause.text) for clause in clauses]
        assert numbers == CN_ARTICLES[: CLAUSES_PER_PROFILE["v2"]]
        assert set(row["expected_risk_article_nos"]) <= set(numbers)


def test_risk_types_and_manifest_counts(tmp_path: Path) -> None:
    out_dir = tmp_path / "dataset"
    records = generate_dataset(out_dir, seed=DEFAULT_SEED)
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

    risk_types = {risk.risk_type for record in records for risk in record.risks}
    assert risk_types == {
        "payment",
        "liability",
        "termination",
        "jurisdiction",
        "confidentiality_scope",
        "force_majeure",
        "liability_cap",
        "acceptance_standard",
    }
    assert manifest["count"] == 18
    assert manifest["risk_injection_total"] == 30
    assert manifest["seed"] == DEFAULT_SEED
    assert manifest["profile"] == "v2"
    assert "非律师标注" in manifest["gold_method"]


def test_v1_profile_stays_at_stage1_shape(tmp_path: Path) -> None:
    out_dir = tmp_path / "v1"
    records = generate_dataset(out_dir, seed=DEFAULT_SEED, profile="v1")
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    gold = _read_gold(out_dir / "gold.jsonl")

    assert len(records) == len(gold) == 12
    assert manifest["profile"] == "v1"
    assert manifest["risk_injection_total"] == 18
    assert manifest["clauses_per_contract"] == 8
    assert {risk.risk_type for record in records for risk in record.risks} == {
        "payment",
        "liability",
        "termination",
        "jurisdiction",
    }
    for row in gold:
        clauses = segment_clauses(parse_document(out_dir / row["file"]))
        assert len(clauses) == 8
        assert [article_no_of(clause.text) for clause in clauses] == CN_ARTICLES[:8]


def test_synthetic_contract_runs_end_to_end(service, tmp_path: Path) -> None:
    out_dir = tmp_path / "dataset"
    generate_dataset(out_dir, seed=DEFAULT_SEED)
    gold = _read_gold(out_dir / "gold.jsonl")
    sample = out_dir / gold[0]["file"]

    result = service.analyze(
        sample,
        engine="mock",
        as_of="2026-11-03",
        require_confirmation=False,
        auto_approve=True,
    )
    assert result.status == "completed"
    assert result.engine == "mock"
    assert result.findings_count > 0
    assert Path(result.docx_path or "").exists()
    assert service.store.count_findings(result.matter_id) == result.findings_count
    document = Document(result.docx_path)
    assert len(document.tables[0].rows) - 1 == result.findings_count
