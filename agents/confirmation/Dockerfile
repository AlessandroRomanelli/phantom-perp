FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY libs/ libs/
COPY agents/confirmation/ agents/confirmation/
COPY configs/ configs/

CMD ["python", "-m", "agents.confirmation.main"]
