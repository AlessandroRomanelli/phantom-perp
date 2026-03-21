FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY libs/ libs/
COPY agents/risk/ agents/risk/
COPY configs/ configs/

CMD ["python", "-m", "agents.risk.main"]
