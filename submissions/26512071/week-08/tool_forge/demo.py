"""Offline, deterministic ReAct-shaped demo of the tool forge.

No LLM is called and no API key is read: the "agent" is a fixed script of
thought/action pairs. Every observation printed is the real return value of
Forge / the registered proxy / a fresh child interpreter.

    python demo.py                  # new library dir under verification/
    python demo.py --library PATH   # reuse PATH (shows persistence across runs)

Each event is one JSON line on stdout. Exit code 0 only if every expected
observation matched.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

from forge import Forge, ToolInvocationError

HERE = os.path.dirname(os.path.abspath(__file__))

BAD_ADD = "def add(a, b):\n    return a - b\n"
GOOD_ADD = "def add(a, b):\n    return a + b\n"
ADD_TEST = "assert add(2, 3) == 5\n"
DESC = "Add two numbers."

# A fresh interpreter rebuilds TOOLS from the library directory alone.
RESTART_CHILD = (
    "import json, os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from forge import Forge\n"
    "forge = Forge(sys.argv[2], mode='restricted')\n"
    "out = {'pid': os.getpid(), 'restored': sorted(forge.TOOLS),\n"
    "       'restore_errors': forge.restore_errors}\n"
    "if 'add' in forge.TOOLS:\n"
    "    out['add(4, 5)'] = forge.TOOLS['add'][1](4, 5)\n"
    "print(json.dumps(out))\n"
)

failures = []


def emit(step, **event):
    print(json.dumps(dict({"step": step}, **event), ensure_ascii=False, sort_keys=True))
    sys.stdout.flush()


def expect(step, label, actual, wanted):
    ok = actual == wanted
    if not ok:
        failures.append(label)
    emit(step, check=label, actual=actual, expected=wanted, ok=ok)


def call(fn, *args):
    try:
        return fn(*args)
    except ToolInvocationError as e:
        return "error:" + str(e)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--library", help="existing or new directory to use as tool_dir")
    opts = parser.parse_args()
    if opts.library:
        library = os.path.abspath(opts.library)
        os.makedirs(library, exist_ok=True)
    else:
        base = os.path.join(HERE, "verification")
        os.makedirs(base, exist_ok=True)
        library = tempfile.mkdtemp(dir=base, prefix="demo-library-")
    emit(0, library=library, parent_pid=os.getpid(), llm_called=False,
         note="scripted agent; library is kept (not deleted) so persistence can be inspected")

    # Default mode asks for an OS sandbox; none exists here, so it fails closed.
    emit(1, thought="(scripted) try the default mode first",
         action="Forge(library).forge_tool('add', ...)")
    expect(1, "default_os_mode_fails_closed",
           Forge(library).forge_tool("add", DESC, GOOD_ADD, ADD_TEST),
           "testfailed:os_sandbox_unavailable")

    forge = Forge(library, mode="restricted")
    emit(2, thought="(scripted) I need to add 2 and 3; is there a tool?",
         action="lookup TOOLS['add']",
         observation="tool_present_from_restore:add" if "add" in forge.TOOLS
         else "tool_missing:add",
         restore_errors=forge.restore_errors)

    emit(3, thought="(scripted) write a tool (first draft has a bug)",
         action="forge_tool('add', code=%r, test=%r)" % (BAD_ADD, ADD_TEST))
    expect(3, "bad_forge_rejected", forge.forge_tool("add", DESC, BAD_ADD, ADD_TEST),
           "testfailed:assertion_failed:1")

    emit(4, thought="(scripted) the observation says assert 1 failed; fix '-' to '+'",
         action="forge_tool('add', code=%r, test=%r)" % (GOOD_ADD, ADD_TEST))
    expect(4, "corrected_forge_registered", forge.forge_tool("add", DESC, GOOD_ADD, ADD_TEST),
           "registered:add")
    emit(4, observation="files", listing=sorted(n for n in os.listdir(library)
                                                if not n.startswith(".")))

    emit(5, thought="(scripted) call the registered tool",
         action="TOOLS['add'][1](2, 3)",
         registry_entry=repr(forge.TOOLS["add"]),
         note="TOOLS holds (description, proxy); each call is a child-process RPC to worker.py")
    expect(5, "invoke_add_2_3", call(forge.TOOLS["add"][1], 2, 3), 5)

    emit(6, thought="(scripted) next launch: does the tool survive a restart?",
         action="fresh python process: Forge(library, mode='restricted')")
    proc = subprocess.run([sys.executable, "-c", RESTART_CHILD, HERE, library],
                          cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=120, check=False)
    try:
        child = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
    except (ValueError, IndexError):
        child = {"returncode": proc.returncode,
                 "stderr_tail": proc.stderr.decode("utf-8", "replace")[-500:]}
    emit(6, observation="restart_child", child=child,
         child_pid_differs=child.get("pid") not in (None, os.getpid()))
    expect(6, "restart_restored_add_4_5", child.get("add(4, 5)"), 9)
    expect(6, "restart_no_restore_errors", child.get("restore_errors"), [])

    emit(7, done=not failures, failures=failures,
         scope="demo of forge.py only; not wired into any market/runtime agent")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
