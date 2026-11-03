#!/bin/sh
# Everything below runs under qemu-x86_64 -cpu phenom,-fxsr-opt (the wrappers in /phenom).
# docker run --rm -v <scratchpad>/stubapi:/stub week08-phenom:merged  (stub only for the claude -p call)
step() { echo "\$ $*"; "$@" 2>&1; echo "[exit $?]"; }
step sh -c "/phenom/run /target/usr/lib64/ld-linux-x86-64.so.2 --help | sed -n '/glibc-hwcaps/,\$p'"
step /phenom/python /phenom/phenom_check.py
step git --version
step ssh -V
step /phenom/run /target/usr/local/bin/cloudflared --version
step /phenom/node --version
CLI=/target/usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js
step /phenom/node $CLI --version
step sh -c "/phenom/node $CLI --help | grep -E -- '--(model|tools|setting-sources|no-session-persistence) '"
if [ -f /stub/stub.py ]; then
    /phenom/python /stub/stub.py & STUB=$!
    sleep 3
    before=$(wc -l < /stub/requests.log)
    echo '$ echo "Reply with OK." | node cli.js -p --model claude-opus-5-5 --tools "" --setting-sources "" --no-session-persistence (stub API)'
    echo "Reply with OK." | ANTHROPIC_BASE_URL=http://127.0.0.1:8787 ANTHROPIC_API_KEY=sk-stub CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
        DISABLE_AUTOUPDATER=1 /phenom/node $CLI -p --model claude-opus-5-5 --tools "" --setting-sources "" --no-session-persistence 2>&1
    echo "[exit $?]"
    echo "== new lines in /stub/requests.log"
    tail -n +$((before + 1)) /stub/requests.log
    kill $STUB
fi
echo "== shop tests (/opt/agent/tests)"
step /phenom/python -m unittest discover -s tests
echo "== strategist tests (/opt/agent/strategist/tests)"
step /phenom/python -m unittest discover -s strategist/tests -t .
