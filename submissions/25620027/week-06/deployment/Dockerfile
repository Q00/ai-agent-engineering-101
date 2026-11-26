FROM ghcr.io/astral-sh/uv:0.7.19 AS uv
FROM python:3.12.11-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY harness_app.py harness_app.py.lock ./
RUN uv export --script harness_app.py --frozen --no-dev --output-file /tmp/requirements.txt \
    && uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python --require-hashes -r /tmp/requirements.txt

FROM python:3.12.11-slim-bookworm
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home seller \
    && mkdir /data && chown seller:seller /data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY public_seller.py harness_app.py http_client.py catalog.json ./
COPY market_a2a/*.py ./market_a2a/
USER 10001:10001
EXPOSE 9998
CMD ["python", "public_seller.py"]
