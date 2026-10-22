FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 10001 atlas
WORKDIR /app

COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
RUN pip install --no-cache-dir --no-deps .
RUN mkdir -p /app/state /app/data && chown -R atlas:atlas /app/state /app/data

USER atlas
EXPOSE 8000
CMD ["sh", "-c", "if [ \"$ATLAS_ENV\" = \"development\" ]; then uvicorn ${ATLAS_APP_MODULE:-algotrader.api.portal_app:app} --host 0.0.0.0 --port 8000 --reload; else uvicorn ${ATLAS_APP_MODULE:-algotrader.api.portal_app:app} --host 0.0.0.0 --port 8000 --workers 1; fi"]
