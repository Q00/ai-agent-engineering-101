# Seller in a container. The Codex login is NOT baked in: mount it at run time (see REPORT or the commands below).
#   docker build -t codex-shop .
#   docker run --rm -p 127.0.0.1:9000:9000 -v ~/.codex-docker:/codex-home codex-shop --public-url https://xxxx.trycloudflare.com/

# Codex CLI from npm, same version as the passing trade test (agent.json).
FROM node:22-bookworm-slim AS codex
RUN npm install -g @openai/codex@0.162.1

FROM python:3.12-slim-bookworm
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /usr/local/lib/node_modules/@openai/codex /usr/local/lib/node_modules/@openai/codex
RUN ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex

RUN pip install --no-cache-dir "a2a-sdk[http-server]==1.2.2" uvicorn==0.54.0 starlette==1.7.0 httpx==0.28.1

ENV CODEX_HOME=/codex-home PYTHONUNBUFFERED=1
WORKDIR /app
COPY harness.py seller.py ./
COPY prompts/ prompts/

EXPOSE 9000
ENTRYPOINT ["python", "seller.py", "--host", "0.0.0.0", "--port", "9000"]
