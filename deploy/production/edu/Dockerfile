FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /srv/engine
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 engine && useradd --uid 10001 --gid engine --no-create-home engine
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir --upgrade pip==26.2.2 && pip install --no-cache-dir -r requirements.lock.txt && pip check
RUN mkdir -p /home/engine && chown 10001:10001 /home/engine
COPY --chown=10001:10001 edudss_engine ./edudss_engine
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "edudss_engine.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
