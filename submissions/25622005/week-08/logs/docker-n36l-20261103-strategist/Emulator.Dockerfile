# Local verification only. No keys, claim code, model calls or market access.
# Same method as ../docker-n36l-20261102-1/Emulator.Dockerfile: Docker Desktop runs amd64 through
# Rosetta, which ignores QEMU_CPU, so the image's root file system runs under qemu-user with K10 (Phenom).
# -fxsr-opt only silences a TCG warning (fast FXSAVE); it does not change the instruction set.
FROM --platform=linux/amd64 week08-strategist:n36l AS candidate
FROM --platform=linux/arm64 debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends qemu-user \
    && rm -rf /var/lib/apt/lists/*
COPY --from=candidate / /target/
# The loader link is absolute (/lib/x86_64-linux-gnu/...), which the arm64 host cannot resolve.
RUN cp --remove-destination /target/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /target/usr/lib64/ld-linux-x86-64.so.2
# Wrappers outside /target, so children started through sys.executable stay under the same CPU model.
RUN mkdir -p /phenom \
    && printf '#!/bin/sh\nexec qemu-x86_64 -cpu phenom,-fxsr-opt -L /target -0 /phenom/python /target/usr/local/bin/python3.12 "$@"\n' > /phenom/python \
    && printf '#!/bin/sh\nexec qemu-x86_64 -cpu phenom,-fxsr-opt -L /target /target/usr/local/bin/node "$@"\n' > /phenom/node \
    && printf '#!/bin/sh\nexec qemu-x86_64 -cpu phenom,-fxsr-opt -L /target "$@"\n' > /phenom/run \
    && chmod 755 /phenom/python /phenom/node /phenom/run
COPY phenom_check.py /phenom/phenom_check.py
WORKDIR /target/opt/strategist
