from pathlib import Path

import pytest

from lexflow.config import ROOT, Settings
from lexflow.service import AnalyzeService


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        db_path=tmp_path / "data" / "lexflow.db",
        corpus_path=ROOT / "data" / "corpus" / "law_articles.jsonl",
        failures_path=tmp_path / "data" / "failures.jsonl",
        template_path=tmp_path / "assets" / "risk_report.docx",
        embedding_enabled=False,
    )


@pytest.fixture()
def service(settings: Settings) -> AnalyzeService:
    return AnalyzeService(settings)
