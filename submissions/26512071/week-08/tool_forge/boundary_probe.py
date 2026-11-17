"""Boundary probe: what mode="restricted" does and does NOT isolate.

Part A (generated code): adversarial tool/test sources that try to open a
socket to a parent-owned 127.0.0.1 listener, resolve example.com, write a
marker file outside tool_dir, spawn a process, or reach builtins through
reflection/imports are submitted through Forge.forge_tool. They are only
ever parsed and interpreted by worker.py in a child; this file never
executes them. Expected: every one is rejected, nothing is registered or
published, the listener sees no connection, the marker never appears.

Part B (trusted control): DIAGNOSTIC_SOURCE below is fixed, task-authored
trusted diagnostic code (NOT an untrusted forged tool). It runs in a child launched like the worker
(sys.executable -I -S, minimal env, task-local cwd/TEMP) and really calls
socket.connect to the parent's listener and getaddrinfo('example.com', 443).
If the connect succeeds, a normal child of this setup HAS OS network
capability, i.e. there is no OS-level no-network sandbox here.

Only the own localhost listener and public DNS are touched: no HTTP, no
market, no credentials, no firewall or OS changes. A DNS failure alone is
reported as inconclusive, never as proof of a deny.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

from forge import Forge, WALL_TIMEOUT_SECONDS

HERE = os.path.dirname(os.path.abspath(__file__))

# Trusted diagnostic child. argv: port token
DIAGNOSTIC_SOURCE = r'''
import json, os, socket, sys
port, token = int(sys.argv[1]), sys.argv[2]
out = {"pid": os.getpid()}
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(("127.0.0.1", port))
    s.sendall(("DIAG pid=%d token=%s" % (os.getpid(), token)).encode("ascii"))
    s.close()
    out["local_connect"] = {"ok": True}
except Exception as e:
    out["local_connect"] = {"ok": False, "error": type(e).__name__,
                            "errno": getattr(e, "errno", None)}
try:
    infos = socket.getaddrinfo("example.com", 443, proto=socket.IPPROTO_TCP)
    out["dns_example_com"] = {"ok": True, "addresses": len(infos)}
except Exception as e:
    out["dns_example_com"] = {"ok": False, "error": type(e).__name__,
                              "errno": getattr(e, "errno", None)}
print(json.dumps(out))
'''

STATIC_PREFIXES = ("ast:", "code:", "test:")


class Listener:
    """Parent-owned 127.0.0.1 listener recording every accepted connection."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.connections = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                conn, peer = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn.settimeout(2)
            data = b""
            try:
                while len(data) < 512:
                    chunk = conn.recv(512)
                    if not chunk:
                        break
                    data += chunk
            except OSError:
                pass
            conn.close()
            self.connections.append({"peer": "%s:%d" % peer,
                                     "payload": data.decode("ascii", "replace")})

    def close(self):
        self._stop.set()
        self._thread.join(5)
        self.sock.close()


def adversarial_cases(port, marker):
    ok_test = "assert p(1) == 1\n"
    good = "def p(x):\n    return x\n"

    def body(expr):
        return "def p(x):\n    return %s\n" % expr

    return [
        ("local_connect", body("socket.socket().connect(('127.0.0.1', %d))" % port), ok_test),
        ("local_connect_import", "import socket\n" + body(
            "socket.create_connection(('127.0.0.1', %d))" % port), ok_test),
        ("dns_getaddrinfo", body("socket.getaddrinfo('example.com', 443)"), ok_test),
        ("dunder_import_socket", body(
            "__import__('socket').create_connection(('127.0.0.1', %d))" % port), ok_test),
        ("file_write_outside", body("open(%r, 'w').write('x')" % marker), ok_test),
        ("subprocess_spawn", body("__import__('subprocess').Popen(['cmd', '/c', 'echo'])"),
         ok_test),
        ("reflection_subclasses", body("().__class__.__bases__[0].__subclasses__()"), ok_test),
        ("builtins_subscript", body("__builtins__['open'](%r, 'w')" % marker), ok_test),
        ("body_import_statement",
         "def p(x):\n    import socket\n    return x\n", ok_test),
        ("test_side_connect", good,
         "assert p(1) == 1 or __import__('socket').create_connection(('127.0.0.1', %d))\n"
         % port),
        ("test_side_open", good, "assert p(1) == 1\nassert open(%r, 'w')\n" % marker),
    ]


def run_restricted(tool_dir, port, marker):
    forge = Forge(tool_dir, mode="restricted")
    results = []
    for label, code, test in adversarial_cases(port, marker):
        result = forge.forge_tool("p", "probe", code, test)
        reason = result[len("testfailed:"):] if result.startswith("testfailed:") else None
        results.append({
            "case": label,
            "result": result,
            "rejected": reason is not None,
            "static_reject": bool(reason and reason.startswith(STATIC_PREFIXES)),
            "registered": "p" in forge.TOOLS,
            "published": os.path.lexists(os.path.join(tool_dir, "p.py")),
        })
    default = Forge(tool_dir).forge_tool("p", "probe", "def p(x):\n    return x\n",
                                         "assert p(1) == 1\n")
    return results, default


def run_diagnostic(workdir, port, token):
    env = {"TEMP": workdir, "TMP": workdir}
    for key in ("SystemRoot", "WINDIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-c", DIAGNOSTIC_SOURCE, str(port), token],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=workdir, env=env,
            timeout=30 + WALL_TIMEOUT_SECONDS, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        return {"spawn_error": type(e).__name__}
    try:
        return json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"returncode": proc.returncode,
                "stderr_tail": proc.stderr.decode("utf-8", "replace")[-500:]}


def main():
    try:
        listener = Listener()
    except OSError as e:
        print(json.dumps({"status": "blocked", "reason": "listener_bind_failed:" +
                          type(e).__name__, "os_network_isolation": None}))
        return 2
    summary = {"mode": "restricted",
               "boundary": "AST-interpreted capability subset; NOT OS sandbox",
               "parent_pid": os.getpid(), "listener": "127.0.0.1:%d" % listener.port}
    try:
        with tempfile.TemporaryDirectory(dir=HERE, prefix=".probe-") as root:
            tool_dir = os.path.join(root, "tools")
            outside = os.path.join(root, "outside")
            workdir = os.path.join(root, "diag")
            for d in (tool_dir, outside, workdir):
                os.mkdir(d)
            marker = os.path.join(outside, "payload-ran.marker")

            results, default = run_restricted(tool_dir, listener.port, marker)
            time.sleep(0.5)
            summary["restricted_attempts"] = results
            summary["default_os_mode"] = default
            summary["connections_during_restricted"] = list(listener.connections)
            summary["marker_exists"] = os.path.exists(marker)

            token = "probe-%d-%d" % (os.getpid(), int(time.time()))
            before = len(listener.connections)
            diag = run_diagnostic(workdir, listener.port, token)
            deadline = time.time() + 5
            while len(listener.connections) == before and time.time() < deadline:
                time.sleep(0.05)
            received = listener.connections[before:]
    finally:
        listener.close()

    local_ok = bool(diag.get("local_connect", {}).get("ok"))
    payload_ok = any(token in c["payload"] and ("pid=%s " % diag.get("pid")) in c["payload"]
                     for c in received)
    dns = diag.get("dns_example_com", {})
    summary["trusted_diagnostic"] = {
        "source": "module-level DIAGNOSTIC_SOURCE (trusted, not generated)",
        "argv_flags": ["-I", "-S"], "result": diag,
        "child_pid_differs": diag.get("pid") not in (None, os.getpid()),
        "listener_received": received, "marker_payload_matched": payload_ok,
        "dns_label": "resolved" if dns.get("ok") else "inconclusive_failure",
    }
    summary["os_network_isolation"] = False if (local_ok and payload_ok) else None

    checks = {
        "local_control_connect_succeeded": local_ok and payload_ok,
        "control_child_pid_differs": summary["trusted_diagnostic"]["child_pid_differs"],
        "all_restricted_rejected": all(r["rejected"] and not r["registered"]
                                       and not r["published"] for r in results),
        "no_connection_from_restricted": summary["connections_during_restricted"] == [],
        "marker_untouched": not summary["marker_exists"],
        "default_mode_fails_closed": default == "testfailed:os_sandbox_unavailable",
    }
    summary["checks"] = checks
    summary["passed"] = all(checks.values())
    if not local_ok:
        summary["note"] = ("local control connect failed; the probe cannot show OS network "
                           "capability here, and that failure is NOT proof of isolation")
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
