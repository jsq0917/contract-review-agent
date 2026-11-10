"""集中配置：所有路径、模型开关、成本单价均从环境变量读取。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """加载项目根目录 .env；已存在的环境变量优先，不被覆盖。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip("\"'"))


_load_dotenv(ROOT / ".env")

# 本地模型目录：默认取项目父目录下的“模型”（本机即 E:\project\模型）；
# 容器内用 LEXFLOW_MODEL_DIR=/models。代码不写死 Windows 路径。
MODEL_DIR = Path(os.getenv("LEXFLOW_MODEL_DIR", ROOT.parent / "模型"))
LOCAL_BGE_M3 = MODEL_DIR / "bge-m3"
LOCAL_RERANKER = MODEL_DIR / "bge-reranker-v2-m3"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_embedding_model() -> str:
    configured = os.getenv("LEXFLOW_EMBEDDING_MODEL", "")
    if configured:
        return configured
    return str(LOCAL_BGE_M3) if LOCAL_BGE_M3.exists() else "BAAI/bge-small-zh-v1.5"


def _default_reranker_model() -> str:
    configured = os.getenv("LEXFLOW_RERANKER_MODEL", "")
    if configured:
        return configured
    return str(LOCAL_RERANKER) if LOCAL_RERANKER.exists() else ""


@dataclass
class Settings:
    """运行配置。注意：api_key 为空时只能使用 mock provider。"""

    data_dir: Path = field(default_factory=lambda: Path(os.getenv("LEXFLOW_DATA_DIR", ROOT / "data")))
    artifacts_dir: Path = field(
        default_factory=lambda: Path(os.getenv("LEXFLOW_ARTIFACTS_DIR", ROOT / "artifacts"))
    )
    db_path: Path = field(default_factory=lambda: Path(os.getenv("LEXFLOW_DB", ROOT / "data" / "lexflow.db")))
    corpus_path: Path = field(
        default_factory=lambda: Path(os.getenv("LEXFLOW_CORPUS", ROOT / "data" / "corpus" / "law_articles.jsonl"))
    )
    failures_path: Path = field(
        default_factory=lambda: Path(os.getenv("LEXFLOW_FAILURES", ROOT / "data" / "failures.jsonl"))
    )
    template_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("LEXFLOW_TEMPLATE", ROOT / "assets" / "templates" / "risk_report.docx")
        )
    )

    api_key: str = field(default_factory=lambda: os.getenv("LEXFLOW_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv("LEXFLOW_BASE_URL", "https://api.deepseek.com"))
    model: str = field(default_factory=lambda: os.getenv("LEXFLOW_MODEL", "deepseek-chat"))
    # 单价按 1M token 计；未配置时不估算金额，只记录 token 原始计数。
    price_input_per_m: str = field(default_factory=lambda: os.getenv("LEXFLOW_PRICE_INPUT_PER_M", ""))
    price_output_per_m: str = field(default_factory=lambda: os.getenv("LEXFLOW_PRICE_OUTPUT_PER_M", ""))

    # 条款审查时检索候选法条的数量；默认 3 保持既有行为。
    statute_top_k: int = field(default_factory=lambda: int(os.getenv("LEXFLOW_STATUTE_TOP_K", "3")))

    embedding_model: str = field(default_factory=_default_embedding_model)
    embedding_enabled: bool = field(default_factory=lambda: _env_bool("LEXFLOW_EMBEDDING_ENABLED", True))
    embedding_device: str = field(
        default_factory=lambda: os.getenv(
            "LEXFLOW_EMBEDDING_DEVICE", os.getenv("LEXFLOW_DEVICE", "auto")
        )
    )
    device: str = field(default_factory=lambda: os.getenv("LEXFLOW_DEVICE", "auto"))
    embedding_cache_dir: Path = field(
        default_factory=lambda: Path(os.getenv("LEXFLOW_EMBED_CACHE", ROOT / "data" / "embeddings"))
    )
    # 向量层运行时故障的自愈策略：连续失败多少次后转终态、两次重试之间的冷却秒数。
    embed_recover_retries: int = field(
        default_factory=lambda: int(os.getenv("LEXFLOW_EMBED_RECOVER_RETRIES", "3"))
    )
    embed_recover_cooldown_s: float = field(
        default_factory=lambda: float(os.getenv("LEXFLOW_EMBED_RECOVER_COOLDOWN_S", "30"))
    )
    reranker_model: str = field(default_factory=_default_reranker_model)
    reranker_enabled: bool = field(default_factory=lambda: _env_bool("LEXFLOW_RERANKER_ENABLED", False))
    reranker_device: str = field(
        default_factory=lambda: os.getenv(
            "LEXFLOW_RERANKER_DEVICE", os.getenv("LEXFLOW_DEVICE", "auto")
        )
    )
    # 启动时在后台加载精排模型：把 20s 的加载从“首个请求”挪到“服务启动”。
    # 默认开；显存紧张或根本不打算用精排时设 LEXFLOW_RERANKER_WARMUP=false。
    reranker_warmup: bool = field(default_factory=lambda: _env_bool("LEXFLOW_RERANKER_WARMUP", True))

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.failures_path.parent.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
