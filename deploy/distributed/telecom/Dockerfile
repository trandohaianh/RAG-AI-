FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip check && useradd --uid 10001 --no-create-home telecom
COPY ram_online ./ram_online
RUN python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. ram_online/call.proto
USER 10001:10001
CMD ["python", "-m", "ram_online.grpc_server"]
