FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.2 \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check

COPY . .

ENV DATA_DIR=/app/data
ENV PYTHONUNBUFFERED=1

ENV PORT=8000
EXPOSE 8000

# Docker/compose biết khi nào API thật sự sẵn sàng (Render/Railway dùng healthcheckPath riêng).
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5   CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health', timeout=5).status==200 else 1)"

# Render/Railway inject $PORT
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
