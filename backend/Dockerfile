FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN sed -i 's#http://deb.debian.org/debian#https://mirrors.cloud.tencent.com/debian#g' /etc/apt/sources.list.d/debian.sources \
  && sed -i 's#http://deb.debian.org/debian-security#https://mirrors.cloud.tencent.com/debian-security#g' /etc/apt/sources.list.d/debian.sources \
  && apt-get update \
  && apt-get install -y --no-install-recommends build-essential curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN useradd --create-home --shell /bin/bash appuser

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY scripts ./scripts

RUN printf 'FundScope backend\n' > /README.md

RUN pip install --no-cache-dir \
  -i https://mirrors.cloud.tencent.com/pypi/simple \
  --trusted-host mirrors.cloud.tencent.com \
  .

RUN mkdir -p /usr/local/lib/python3.11/site-packages/efinance/data \
  && chown -R appuser:appuser /usr/local/lib/python3.11/site-packages/efinance/data

USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
