# syntax=docker/dockerfile:1
# 模型权重不进入镜像；通过 bind mount 挂到 /models（只读）。
# 基础镜像使用本机已有的 python:3.12-slim-bookworm；Docker Hub 不可达时可离线复用。
FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 默认装 CPU 版 torch，镜像更小、无 GPU 也能启动。
# 需要 GPU 时有两种方式：
#   1) 宿主机预下载 Linux cu130 wheel 放到 docker/wheels/，构建时直接安装（推荐，容器内不需要访问 PyTorch index）；
#   2) 传 build arg 在线安装：
#      docker compose build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu130
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

# 先装依赖，利用镜像层缓存。
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY docker/wheels /tmp/torch-wheels
RUN --mount=type=cache,target=/root/.cache/uv \
    if ls /tmp/torch-wheels/*.whl >/dev/null 2>&1; then \
      echo "installing torch from local wheels"; \
      uv pip install --python /app/.venv/bin/python /tmp/torch-wheels/*.whl; \
    else \
      uv pip install --python /app/.venv/bin/python --index-url ${TORCH_INDEX} torch; \
    fi
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python sentence-transformers faiss-cpu

# 只复制运行所需代码与模板；数据、模型、导出物都在运行时挂载。
COPY lexflow ./lexflow
COPY assets ./assets

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000 8501

CMD ["uvicorn", "lexflow.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
