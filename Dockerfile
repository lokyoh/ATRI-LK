FROM python:3.12-slim

WORKDIR /app

# 1. 更新 pip 和系统基础工具
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --upgrade pip setuptools wheel

# 2. 安装指定版本的 Poetry
ENV POETRY_VERSION=2.1.3
RUN pip install "poetry==$POETRY_VERSION"

# 3. 复制依赖文件并安装
COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main

# 4. 安装 Playwright 及其依赖
RUN playwright install chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gnupg \
    && apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 0E98404D386FA1D9 6ED0E7B82643E131 \
    && playwright install-deps \
    && rm -rf /var/lib/apt/lists/* /tmp/*

ENV PYTHONPATH=/app

CMD ["python", "main.py"]