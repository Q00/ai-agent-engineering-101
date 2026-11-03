# Build from the repository root; compose.yaml supplies that context.
# Target: HP N36L, AMD Athlon II Neo (K10, family 16). It has SSE3 and SSE4a but no
# SSSE3, SSE4.1, SSE4.2 or AVX, so every native part must run on baseline x86-64.
ARG CLOUDFLARED_VERSION=2026.10.2
FROM cloudflare/cloudflared:${CLOUDFLARED_VERSION} AS tunnel
FROM node:22-bookworm-slim AS node

# Official CPython is built without -march flags; same minor version as the tested Mac .venv.
# Node.js is here only to run the Claude Code CLI: the strategist's strategy call
# and the kit hooks' negotiation fallback (EXCEPTION_HARNESS_CMD).
FROM python:3.12.11-slim-bookworm
# Last npm release with the Node.js CLI; newer ones run a native binary that
# fails with Illegal instruction on K10 (week-07 DEPLOY_NAS.md, N40L section).
ARG CLAUDE_CODE_VERSION=2.1.112

# git and ssh push the strategist's records to a separate repository (strategist/publish.py); Debian builds, baseline x86-64.
# HOME is /data/home on the persistent volume: gosu sets HOME from this passwd entry, so the
# Claude login and the publish deploy key survive new images and are shared by the kit and the strategist.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git gosu openssh-client tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --home-dir /data/home --no-create-home --uid 1000 --user-group agent
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DISABLE_AUTOUPDATER=1

COPY submissions/25622005/week-08/docker/requirements.txt /opt/deploy/requirements.txt
# Wheels only: a source build could pick up compiler flags for a newer build machine.
RUN pip install --no-cache-dir --root-user-action=ignore --only-binary=:all: \
        -r /opt/deploy/requirements.txt
COPY --from=tunnel /usr/local/bin/cloudflared /usr/local/bin/cloudflared

# Only the node binary and the npm package; the npm link is recreated because
# copying it from the node stage would copy the target file instead of the link.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && node --version \
    && npm --version
# Do not pull native sharp modules (the model calls send text only).
# Require cli.js so changing the version cannot silently restore the native binary.
RUN npm install --global --omit=optional "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && test -f /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js \
    && ln -sf /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js /usr/local/bin/claude \
    && claude --version \
    && npm cache clean --force

# GitHub's published SSH host keys (https://api.github.com/meta, 2026-11-03), so the first push
# cannot be answered by another host; publish.py's own known_hosts covers any other remote.
COPY submissions/25622005/week-08/docker/github_known_hosts /etc/ssh/ssh_known_hosts
# Dockerfile.dockerignore lets through only code, tests (with the strategist's fixtures) and strategy/SKILL.md.
COPY submissions/25622005/week-08/ /opt/agent/
COPY submissions/25622005/week-08/docker/ /opt/deploy/
# Catch argument-parser, strategy and unit test errors before publishing an image.
RUN python /opt/agent/market_runtime.py --help > /dev/null \
    && python -c "import sys; sys.path.insert(0, '/opt/agent'); import policy; \
front, _ = policy.split_skill(open('/opt/agent/strategy/SKILL.md', encoding='utf-8').read()); \
errors = policy.validate(front.get('policy')); sys.exit('\n'.join(errors) if errors else 0)"
RUN cd /opt/agent \
    && python -m strategist --help > /dev/null \
    && python -m unittest discover -s tests \
    && python -m unittest discover -s strategist/tests -t /opt/agent
# as-agent: `docker compose exec` starts as root; this runs a command as agent (see compose.yaml).
RUN chmod 755 /opt/deploy/entrypoint.sh \
    && install -m 755 /opt/deploy/as-agent /usr/local/bin/as-agent \
    && mkdir -p /data \
    && chown agent:agent /data

WORKDIR /data
# The entrypoint fixes bind-mount ownership, then drops to the configured user ID.
ENTRYPOINT ["/opt/deploy/entrypoint.sh"]
CMD ["run"]
