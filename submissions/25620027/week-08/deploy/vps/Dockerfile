FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.12.11-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
COPY requirements-kit.lock ./
RUN uv pip install --system --no-deps -r requirements-kit.lock \
    && useradd --uid 10001 --create-home app
COPY . /app/
RUN ln -s /data/r3/shop_key.pem /app/shop_key.pem \
    && ln -s /data/r3/comparison-cache /app/.market
USER 10001:10001
ENV AX_MODEL_PROVIDER=openrouter-free AX_OPENAI_FALLBACK=0 AX_PRICE_COMPARISON=0
ENTRYPOINT ["python", "-m", "deploy.vps.launch"]
CMD ["--config", "/private/runtime.json"]
