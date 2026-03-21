FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[monitoring]" 2>/dev/null || pip install --no-cache-dir -e .
COPY libs/ libs/
COPY agents/monitoring/ agents/monitoring/
COPY configs/ configs/

CMD ["python", "-m", "agents.monitoring.main"]
