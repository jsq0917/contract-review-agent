"""统一的 device 解析层：cuda 可用则用，不可用自动降 cpu。

所有本地模型（embedding / reranker）都通过这里决定设备，避免各自硬编码。
"""

from __future__ import annotations

import os


def resolve_device(preferred: str = "auto") -> str:
    """preferred 为 auto 时按 CUDA 可用性解析；显式 cpu/cuda 时原样返回。"""
    preferred = (preferred or "auto").strip().lower()
    if preferred != "auto":
        return preferred
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def device_report(preferred: str = "auto") -> dict[str, object]:
    """返回可写入日志/health 的设备事实，不做任何推断性描述。"""
    resolved = resolve_device(preferred)
    report: dict[str, object] = {"requested": preferred, "resolved": resolved, "torch": "", "cuda_available": False, "device_count": 0}
    try:
        import torch

        report["torch"] = torch.__version__
        report["cuda_available"] = bool(torch.cuda.is_available())
        report["device_count"] = int(torch.cuda.device_count())
        if torch.cuda.is_available():
            report["device_name"] = torch.cuda.get_device_name(0)
    except ImportError:
        report["torch"] = "未安装"
    return report


def describe_env_device() -> dict[str, object]:
    return device_report(os.getenv("LEXFLOW_DEVICE", "auto"))
