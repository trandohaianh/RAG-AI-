FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
WORKDIR /srv/ui
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip check && useradd --uid 10001 --no-create-home ui
COPY app.py api_pool.py ./
USER 10001:10001
CMD ["streamlit","run","app.py","--server.address=0.0.0.0","--server.port=8501","--server.headless=true","--server.enableXsrfProtection=true","--server.enableCORS=true","--browser.gatherUsageStats=false","--server.maxUploadSize=5"]
