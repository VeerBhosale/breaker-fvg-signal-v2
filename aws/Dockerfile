FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV V2_RUNTIME_MODE=aws_readiness_audit
ENV V2_RUNTIME_CONFIG=/app/Newtest/Breaker_Based/signal_model_v2/configs/v2_runtime_config.example.json

WORKDIR /app

# Build context must be the repository root, not the aws/ folder.
COPY Newtest/Breaker_Based/signal_model_v2/aws/requirements-v2.txt /tmp/requirements-v2.txt
RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir -r /tmp/requirements-v2.txt

COPY . /app

RUN python -m py_compile Newtest/Breaker_Based/signal_model_v2/scripts/v2_audit_system.py

CMD ["python", "Newtest/Breaker_Based/signal_model_v2/scripts/v2_aws_readiness_audit.py", "--run-id", "v2_aws_container_startup_readiness"]
