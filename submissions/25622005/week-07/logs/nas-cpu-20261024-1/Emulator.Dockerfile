# Local verification only. No host credentials, model calls or market registration.
FROM --platform=linux/amd64 week07-agent:n40l AS candidate
FROM --platform=linux/arm64 debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends qemu-user \
    && rm -rf /var/lib/apt/lists/*
COPY --from=candidate /usr/local/bin/node /target/node
COPY --from=candidate /usr/local/lib/node_modules/@anthropic-ai/claude-code /target/claude-code
COPY --from=candidate /lib/x86_64-linux-gnu/ /target/lib/x86_64-linux-gnu/
RUN mkdir -p /target/lib64 \
    && cp /target/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /target/lib64/ld-linux-x86-64.so.2
ENV DISABLE_AUTOUPDATER=1
ENTRYPOINT ["qemu-x86_64", "-cpu", "phenom", "-L", "/target", "-E", "LD_LIBRARY_PATH=/target/lib/x86_64-linux-gnu", "/target/node"]
CMD ["/target/claude-code/cli.js", "--version"]
