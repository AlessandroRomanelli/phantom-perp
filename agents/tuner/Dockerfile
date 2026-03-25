FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir ".[tuner]"

COPY libs/ libs/
COPY agents/tuner/ agents/tuner/
COPY configs/ configs/

# Keep a backup of strategy YAMLs for volume bootstrap (D-03).
# The named volume mounts over /app/configs/strategies/ at runtime,
# so the image-baked originals are preserved at _image_strategies/.
RUN cp -r configs/strategies configs/_image_strategies

CMD ["python", "-m", "agents.tuner.entrypoint"]
