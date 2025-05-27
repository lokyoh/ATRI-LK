FROM python:3.12-slim

WORKDIR /app

ENV POETRY_VERSION=1.8.3
RUN pip install "poetry==$POETRY_VERSION"

COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main \
    && playwright install chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gnupg \
    && apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 0E98404D386FA1D9 6ED0E7B82643E131 \
    && playwright install-deps \
    && rm -rf /var/lib/apt/lists/* /tmp/*

ENV PYTHONPATH=/app

CMD ["python", "main.py"]