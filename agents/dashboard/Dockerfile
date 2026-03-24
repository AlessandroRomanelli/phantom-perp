FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir aiohttp orjson redis

COPY agents/dashboard/ agents/dashboard/

EXPOSE 8080

CMD ["python", "-m", "agents.dashboard.main"]
