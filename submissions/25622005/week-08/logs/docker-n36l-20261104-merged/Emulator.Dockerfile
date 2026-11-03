# Local verification only. No keys, claim code, market access; model calls go only to a local stub API.
# Same method as ../docker-n36l-20261103-strategist-publish/Emulator.Dockerfile, now over the merged
# image (shop + strategist in one): Docker Desktop runs amd64 through Rosetta, which ignores QEMU_CPU,
# so the image's root file system runs under qemu-user with K10 (Phenom).
# -fxsr-opt only silences a TCG warning (fast FXSAVE); it does not change the instruction set.
FROM --platform=linux/amd64 week08-agent:n36l AS candidate
FROM --platform=linux/arm64 debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends qemu-user \
    && rm -rf /var/lib/apt/lists/*
COPY --from=candidate / /target/
# The loader link is absolute (/lib/x86_64-linux-gnu/...), which the arm64 host cannot resolve.
RUN cp --remove-destination /target/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /target/usr/lib64/ld-linux-x86-64.so.2
# Wrappers outside /target, so children started through sys.executable stay under the same CPU model.
RUN mkdir -p /phenom/bin \
    && printf '#!/bin/sh\nexec qemu-x86_64 -cpu phenom,-fxsr-opt -L /target -0 /phenom/python /target/usr/local/bin/python3.12 "$@"\n' > /phenom/python \
    && printf '#!/bin/sh\nexec qemu-x86_64 -cpu phenom,-fxsr-opt -L /target /target/usr/local/bin/node "$@"\n' > /phenom/node \
    && printf '#!/bin/sh\nexec qemu-x86_64 -cpu phenom,-fxsr-opt -L /target "$@"\n' > /phenom/run \
    && printf '#!/bin/sh\nexec qemu-x86_64 -cpu phenom,-fxsr-opt -L /target /target/usr/bin/git "$@"\n' > /phenom/bin/git \
    && printf '#!/bin/sh\nexec qemu-x86_64 -cpu phenom,-fxsr-opt -L /target /target/usr/bin/ssh "$@"\n' > /phenom/bin/ssh \
    && chmod 755 /phenom/python /phenom/node /phenom/run /phenom/bin/git /phenom/bin/ssh
COPY phenom_check.py phenom_run.sh /phenom/
WORKDIR /target/opt/agent
# git runs its helpers (fetch, merge-base, receive-pack, ...) from GIT_EXEC_PATH; wrap each one so
# they also run under qemu with the K10 model instead of escaping to the host's amd64 emulation.
RUN mkdir -p /phenom/git-core \
    && for f in /target/usr/lib/git-core/*; do \
         n=$(basename "$f"); \
         if [ -f "$f" ] && head -c 4 "$f" | grep -q ELF; then \
           printf '#!/bin/sh\nexec qemu-x86_64 -cpu phenom,-fxsr-opt -L /target %s "$@"\n' "$f" > /phenom/git-core/$n; \
         else ln -s "$f" /phenom/git-core/$n; fi; \
       done \
    && chmod 755 /phenom/git-core/*
ENV GIT_EXEC_PATH=/phenom/git-core \
    PATH=/phenom/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CMD ["sh", "/phenom/phenom_run.sh"]
