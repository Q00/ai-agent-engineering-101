# Local verification only. No keys, claim code, model calls or market registration.
# Docker Desktop runs amd64 through Rosetta, which ignores QEMU_CPU, so the candidate
# image's root file system runs under qemu-user with a K10 (Phenom) CPU model instead.
# -fxsr-opt only silences a TCG warning (fast FXSAVE); it does not change the instruction set.
FROM --platform=linux/amd64 week08-agent:n36l AS candidate
FROM --platform=linux/arm64 debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends qemu-user \
    && rm -rf /var/lib/apt/lists/*
COPY --from=candidate / /target/
# The loader link is absolute (/lib/x86_64-linux-gnu/...), which the arm64 host cannot resolve.
RUN cp --remove-destination /target/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /target/usr/lib64/ld-linux-x86-64.so.2
# /phenom/python is outside /target, so qemu does not remap it: Python children started
# through sys.executable come back here and run under the same CPU model.
RUN mkdir -p /phenom \
    && printf '#!/bin/sh\nexec qemu-x86_64 -cpu phenom,-fxsr-opt -L /target -0 /phenom/python /target/usr/local/bin/python3.12 "$@"\n' > /phenom/python \
    && printf '#!/bin/sh\nexec qemu-x86_64 -cpu phenom,-fxsr-opt -L /target "$@"\n' > /phenom/run \
    && chmod 755 /phenom/python /phenom/run \
    && mkdir -p /target/data \
    && cp -R /target/opt/agent/. /target/data/ \
    && rm -rf /target/data/docker \
    && cp /target/opt/deploy/agent.container.json /target/data/agent.json
COPY phenom_check.py /phenom/phenom_check.py
WORKDIR /target/data
