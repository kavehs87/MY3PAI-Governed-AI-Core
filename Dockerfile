FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md Makefile ./
COPY schemas/ schemas/
COPY src/ src/
COPY tests/ tests/
COPY scripts/ scripts/
COPY benchmarks/ benchmarks/

RUN apt-get update && apt-get install -y --no-install-recommends make \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -e ".[dev]"

CMD ["make", "verify"]
