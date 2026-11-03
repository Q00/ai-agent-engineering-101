"""Mock ssh for the codex_driver dry run: NO network, NO real codex. Usage like ssh: mock_ssh.py <remote command>.
Checks the remote command is exactly one `codex exec` with the fixed flags and an existing -C dir, reads the prompt
from stdin and prints a canned answer: parse prompts -> {"offer": N} (or garbage for one message), editor prompts ->
a no-change JSON ({"edits": []}; run=3 -> fenced, a violation), injected prompts -> a non-JSON refusal, 'short timeout' cases are simulated as the remote `timeout` (exit 124 after t s)
(the mock sleeps 3 s on prompts that contain run=99 or 'nine thousand won please' when R3_MOCK_SLOW=1)."""
import json
import os
import shlex
import sys
import time

args = shlex.split(sys.argv[-1])
# v1.17b: the remote command must start with `timeout -k <k> <t>` (codex is killed on labpc, not only the ssh)
if args[:2] != ["timeout", "-k"] or len(args) < 4 or not args[2].isdigit() or not args[3].isdigit():
    print("mock_ssh: remote command is not wrapped in timeout -k K T", file=sys.stderr)
    sys.exit(96)
remote_t = int(args[3])
args = args[4:]
need = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only"]
if args[:1] and args[0].endswith("/codex"):
    args[0] = "codex"
if args[:6] != need or "-C" not in args or args[-1] != "-" or "-m" not in args:
    print("mock_ssh: bad remote command", file=sys.stderr)
    sys.exit(97)
cwd = args[args.index("-C") + 1]
if not os.path.isdir(cwd):
    print(f"mock_ssh: -C {cwd} does not exist", file=sys.stderr)
    sys.exit(98)
if any(a in args for a in ("--dangerously-bypass-approvals-and-sandbox", "--add-dir", "--full-auto")):
    sys.exit(99)
p = sys.stdin.read()
if "run=99" in p or ("<<<MSG\nnine thousand won please" in p and os.environ.get("R3_MOCK_SLOW") == "1"):
    if remote_t < 3:                  # what GNU timeout does on labpc: TERM at t, exit 124
        time.sleep(remote_t)
        sys.exit(124)
    time.sleep(3)
if "SYSTEM:" in p or "new instruction" in p:
    print("I can't follow instructions inside the data.")
elif "<<<MSG" in p:
    if "about six grand" in p:
        print("Sure! The offer is 6000.")                         # format violation on purpose
    else:
        print(json.dumps({"offer": 9000}))
elif "run=3" in p:
    print("```json\n{\"edits\": []}\n```")                     # format violation on purpose
else:
    print(json.dumps({"edits": [], "rationale": "mock: no clear signal"}))
