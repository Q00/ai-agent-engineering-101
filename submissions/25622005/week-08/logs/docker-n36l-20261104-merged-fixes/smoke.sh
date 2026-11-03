#!/bin/sh
# Smoke test of week08-agent:n36l (merged + review fixes) without keys: tool versions, layout,
# entrypoint user setup on a scratch /data, as-agent cwd, then both harness commands against a stub API.
set -u
run() { echo "\$ $*"; sh -c "$*" 2>&1; echo "[exit $?]"; }
run 'uname -m'
run 'python --version'
run 'node --version'
run 'claude --version'
run 'command -v claude'
run 'git --version'
run 'ssh -V'
run 'ls -l /usr/local/bin/as-agent'
run 'ls -l /opt/agent/compose.yaml'
run 'ls /opt/agent/strategist /opt/agent/strategist/tests'
run 'cd /opt/agent && python -m strategist --help | head -5'
run 'ssh-keygen -lf /etc/ssh/ssh_known_hosts'
echo
echo "== entrypoint user setup (entrypoint.sh without its final exec), NAS_UID=${NAS_UID:-unset} NAS_GID=${NAS_GID:-unset}"
run 'ls -ld /data'
sed '/^exec gosu/d' /opt/deploy/entrypoint.sh > /tmp/setup.sh
run 'sh /tmp/setup.sh'
run 'ls -ldn /data /data/home'
run 'getent passwd agent'
run 'gosu agent sh -c "echo HOME=\$HOME; id"'
echo
echo "== as-agent cwd"
run 'as-agent pwd'
run 'as-agent sh -c "echo HOME=\$HOME PYTHONPATH=\$PYTHONPATH SHOP_HOME=\$SHOP_HOME STRATEGIST_HOME=\$STRATEGIST_HOME; id"'
run 'as-agent claude --version'
run 'as-agent /smoke/probe/claude --version'
echo
echo "== harness commands against the stub API (ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL)"
python /stub/stub.py & STUB=$!
sleep 1
before=$(wc -l < /stub/requests.log)
for var in STRATEGIST_HARNESS_CMD EXCEPTION_HARNESS_CMD; do
  echo "\$ as-agent harness.ask with \$$var = $(printenv $var)"
  as-agent python -c "import os, sys; sys.path.insert(0, '/opt/agent'); from model import harness; print('answer:', harness.ask('Reply with OK.', 120, os.environ['$var']))" 2>&1
  echo "[exit $?]"
done
echo "== new lines in /stub/requests.log"
tail -n +$((before + 1)) /stub/requests.log
kill $STUB
