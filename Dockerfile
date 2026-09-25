FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JUSTFASTLLM_HOST=0.0.0.0 \
    JUSTFASTLLM_PORT=8000

WORKDIR /app

COPY pyproject.toml README.md requirements.txt ./
COPY src ./src

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir . \
    && adduser --disabled-password --gecos "" --uid 10001 justfastllm

USER justfastllm

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json, urllib.request; json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2))"

CMD ["python", "-m", "justfastllm"]
