# The kit's runtime only: Python, the kit's dependencies, Claude Code and cloudflared. No code, key or token is baked in;
# compose.yaml mounts them at run time. Versions match the laptop that ran the kit (2026-11-06).
FROM python:3.14-slim

ENV PIP_NO_CACHE_DIR=1 PYTHONUTF8=1 TZ=Asia/Seoul DISABLE_AUTOUPDATER=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
RUN pip install -r /tmp/requirements.lock

# Claude Code, the harness that market_buyer.py and forum.py call (claude -p)
RUN HOME=/opt/claude bash -c 'curl -fsSL https://claude.ai/install.sh | bash -s 2.1.318' \
 && ln -s /opt/claude/.local/bin/claude /usr/local/bin/claude \
 && chmod -R a+rX /opt/claude

# the kit starts cloudflared itself in --tunnel mode
RUN curl -fsSL -o /usr/local/bin/cloudflared \
      https://github.com/cloudflare/cloudflared/releases/download/2026.10.1/cloudflared-linux-amd64 \
 && chmod +x /usr/local/bin/cloudflared

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
