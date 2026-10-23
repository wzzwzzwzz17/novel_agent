# Cloud profile: API-only. No CUDA / llama.cpp / local weights.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEPLOY_PROFILE=cloud \
    LOCAL_LLM_ENABLED=false \
    MODEL_MODE=api_only \
    NOVELAGENT_HOME=/data

COPY pyproject.toml README.md ./
COPY config ./config
COPY plugins ./plugins
COPY src ./src

RUN pip install --no-cache-dir -e .

EXPOSE 8787
VOLUME ["/data"]
CMD ["novel-agent"]
