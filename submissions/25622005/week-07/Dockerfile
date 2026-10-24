# Build from the repository root; compose.yaml supplies that context.
ARG CLOUDFLARED_VERSION=2026.10.2
FROM cloudflare/cloudflared:${CLOUDFLARED_VERSION} AS tunnel

FROM node:22-bookworm-slim
# Last npm release with the Node.js CLI, for the N40L (no AVX/SSE4.2).
ARG CLAUDE_CODE_VERSION=2.1.112

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates git gosu python3 python3-venv tzdata \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/node \
    DISABLE_AUTOUPDATER=1

COPY submissions/25622005/week-07/docker/requirements.txt /opt/deploy/requirements.txt
RUN pip install --no-cache-dir -r /opt/deploy/requirements.txt
# Do not pull native sharp modules (text-only agents do not use them).
# Require cli.js so changing the version cannot silently restore the Bun binary.
RUN npm install --global --omit=optional "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && test -f /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js \
    && ln -sf /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js /usr/local/bin/claude \
    && claude --version \
    && npm cache clean --force
COPY --from=tunnel /usr/local/bin/cloudflared /usr/local/bin/cloudflared

COPY scripts/market_kit.py /opt/market/market_kit.py
COPY submissions/25622005/week-07/*.py /opt/agent/
COPY submissions/25622005/week-07/dns_support/ /opt/agent/dns_support/
COPY submissions/25622005/week-07/tests/ /opt/agent/tests/
COPY submissions/25622005/week-07/docker/ /opt/deploy/
# Catch shallow-path argument-parser failures before publishing an image.
RUN python /opt/agent/market_runtime.py --help > /dev/null
RUN chmod 755 /opt/deploy/entrypoint.sh \
    && mkdir -p /data /home/node \
    && chown node:node /data /home/node

WORKDIR /data
# The entrypoint fixes NAS bind ownership, then drops to the configured user ID.
ENTRYPOINT ["/opt/deploy/entrypoint.sh"]
CMD ["run"]
