FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --home-dir /app appuser
COPY app ./app
COPY static ./static
RUN mkdir -p /data && chown appuser:appuser /data
USER 10001:10001

EXPOSE 8080 1514/udp 1514/tcp 1162/udp 1162/tcp
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
