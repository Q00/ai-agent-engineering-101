FROM cloudflare/cloudflared:2026.10.1 AS tunnel
FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.12.11-slim
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=tunnel /usr/local/bin/cloudflared /usr/local/bin/cloudflared
ENV UV_PYTHON_DOWNLOADS=never UV_CACHE_DIR=/tmp/uv-cache PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 AX_PRICE_COMPARISON=1
WORKDIR /app
COPY harness_app.py harness_app.py.lock http_client.py catalog.json ./
COPY agent.json run_harness.sh ./
COPY market_a2a ./market_a2a
COPY assets ./assets
COPY deploy/vps/guard.py deploy/vps/serve_public.py /app/deploy/vps/
RUN uv export --frozen --script /app/harness_app.py --format requirements.txt --output-file /tmp/requirements.txt \
    && uv pip install --system --require-hashes --no-deps -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && useradd --uid 10001 --create-home app \
    && mkdir -p /run/runtime /app/.market \
    && ln -s /run/runtime/shop_key.pem /app/shop_key.pem
USER 10001:10001
ENV UV_CACHE_DIR=/home/app/.cache/uv
ENTRYPOINT ["python", "/app/deploy/vps/guard.py"]
