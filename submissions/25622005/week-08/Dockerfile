# Build from the repository root; compose.yaml supplies that context.
# Target: HP N36L, AMD Athlon II Neo (K10, family 16). It has SSE3 and SSE4a but no
# SSSE3, SSE4.1, SSE4.2 or AVX, so every native part must run on baseline x86-64.
ARG CLOUDFLARED_VERSION=2026.10.2
FROM cloudflare/cloudflared:${CLOUDFLARED_VERSION} AS tunnel

# Official CPython is built without -march flags; same minor version as the tested Mac .venv.
# No Node.js or Claude Code: the week-08 agent calls no model (llm.* is off in SKILL.md).
FROM python:3.12.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gosu tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --user-group agent
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY submissions/25622005/week-08/docker/requirements.txt /opt/deploy/requirements.txt
# Wheels only: a source build could pick up compiler flags for a newer build machine.
RUN pip install --no-cache-dir --root-user-action=ignore --only-binary=:all: \
        -r /opt/deploy/requirements.txt
COPY --from=tunnel /usr/local/bin/cloudflared /usr/local/bin/cloudflared

# Dockerfile.dockerignore lets through only code, tests and strategy/SKILL.md.
COPY submissions/25622005/week-08/ /opt/agent/
COPY submissions/25622005/week-08/docker/ /opt/deploy/
# Catch argument-parser and strategy errors before publishing an image.
RUN python /opt/agent/market_runtime.py --help > /dev/null \
    && python -c "import sys; sys.path.insert(0, '/opt/agent'); import policy; \
front, _ = policy.split_skill(open('/opt/agent/strategy/SKILL.md', encoding='utf-8').read()); \
errors = policy.validate(front.get('policy')); sys.exit('\n'.join(errors) if errors else 0)"
RUN chmod 755 /opt/deploy/entrypoint.sh \
    && mkdir -p /data \
    && chown agent:agent /data

WORKDIR /data
# The entrypoint fixes bind-mount ownership, then drops to the configured user ID.
ENTRYPOINT ["/opt/deploy/entrypoint.sh"]
CMD ["run"]
