FROM python:3.13-slim

WORKDIR /app

# Install system deps for ta-lib
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential wget && \
    wget -q https://github.com/TA-Lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz && \
    tar xzf ta-lib-0.6.4-src.tar.gz && \
    cd ta-lib-0.6.4 && ./configure --prefix=/usr && make -j$(nproc) && make install && \
    cd .. && rm -rf ta-lib-0.6.4 ta-lib-0.6.4-src.tar.gz && \
    apt-get purge -y build-essential wget && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY libs/ libs/
COPY agents/ingestion/ agents/ingestion/
COPY configs/ configs/

CMD ["python", "-m", "agents.ingestion.main"]
