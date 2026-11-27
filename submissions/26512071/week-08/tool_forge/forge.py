"""Tool Forge: an agent writes a tool, the forge tests it, and only a passing
tool is published to tool_dir and registered in TOOLS.

Security model (educational, deliberately narrow):

* mode="os" (default) asks for a real OS sandbox. None is available here
  (no Docker, no WSL distro), so forge_tool FAILS CLOSED with
  "testfailed:os_sandbox_unavailable" and nothing is restored or executed.
  This module never claims OS isolation.
* mode="restricted" accepts only a small Python-expression subset (see
  worker.py). Generated code is never exec'd, eval'd, compiled or imported,
  in the host or anywhere else: the trusted worker.py parses it to an AST,
  checks an allow-list, and interprets the tree by hand.
* Every test run and every tool call is a fresh child process
  (sys.executable -I -S worker.py) with a minimal environment, a task-local
  cwd/TEMP, JSON on stdin/stdout, a step budget and a wall timeout. A
  separate process limits blast radius of interpreter bugs; it is NOT a
  security boundary like an OS sandbox.

Persistence: <tool_dir>/<name>.py holds the exact source bytes and
<tool_dir>/manifest.json (version 1) lists, per tool, name, description,
code_sha256, the test source and test_sha256, plus the worker policy
version. Files are records; they are never imported, and *.py files that
are not listed in the manifest are ignored (no auto-discovery).

Publication happens only after the tests pass: the source and the new
manifest are staged as temp files in tool_dir, then os.replace moves the
source and then the manifest into place. If the manifest replace fails the
previous source is moved back where practical. This is NOT crash-atomic
across the two files; an interrupted publish leaves a digest mismatch that
restore rejects (fail closed). A fresh name whose <name>.py already exists
without a manifest entry is refused so user files are never overwritten.

Restore (mode="restricted" only) validates the manifest strictly (size,
tool count, exact schema, version, policy, duplicate JSON keys, names,
hashes), reads each listed <name>.py once (regular files only; symlinks and
reparse points are refused where the OS reports them), checks its SHA-256
and UTF-8, re-runs the stored test on those same bytes in a child, and
registers a proxy that keeps those bytes in memory. Failures go to
restore_errors as bounded {"name", "reason"} records; files are left
untouched. An invalid manifest restores nothing and blocks forge_tool.

SHA-256 here detects accidental corruption or stale files only. It is not a
signature: anyone who can write tool_dir can rewrite both a source file and
the manifest, and restore will accept the result if it passes the
restricted worker. tool_dir must be writable only by the trusted user. The
lstat/open check has a small race window and does not cover a symlinked
tool_dir itself.

Single-writer assumption: one Forge instance per tool_dir, used from one
thread. Concurrent writers are not coordinated.
"""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

import worker as _worker

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER_PATH = os.path.join(HERE, "worker.py")
if os.path.normcase(os.path.abspath(_worker.__file__)) != os.path.normcase(WORKER_PATH):
    raise ImportError("forge: imported worker module is not the trusted worker.py")

ForgeError = _worker.ForgeError
check_identifier = _worker.check_identifier
check_value = _worker.check_value

MODES = ("os", "restricted")
WALL_TIMEOUT_SECONDS = 10
MAX_DESCRIPTION_CHARS = 256
MAX_REASON_CHARS = 96
MAX_RESPONSE_BYTES = _worker.MAX_VALUE_BYTES + 1024

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
POLICY_VERSION = "restricted-v1"  # bump when worker.py's accepted subset changes
MANIFEST_KEYS = frozenset(["version", "policy", "tools"])
ENTRY_KEYS = frozenset(["name", "description", "code_sha256", "test", "test_sha256"])
MAX_TOOLS = 64
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_RESTORE_ERRORS = MAX_TOOLS + 1
SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


class ToolInvocationError(RuntimeError):
    """Raised by a registered tool proxy when the worker reports a failure."""


class _DuplicateKey(ValueError):
    pass


def _sanitize(reason):
    text = re.sub(r"[^a-z0-9_:.\-]", "_", str(reason).lower())[:MAX_REASON_CHARS]
    return text or "unknown"


def _reject_constant(_):
    raise ValueError("non-finite number")


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise _DuplicateKey(key)
        obj[key] = value
    return obj


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _precheck(name, description, code, test):
    """Cheap host-side checks; the worker re-validates everything."""
    try:
        check_identifier(name, "name:invalid")
    except ForgeError as e:
        return e.code
    if (type(description) is not str or not description.strip()
            or len(description) > MAX_DESCRIPTION_CHARS or not description.isprintable()):
        return "description:invalid"
    for label, text, limit in (("code", code, _worker.MAX_SOURCE_BYTES),
                               ("test", test, _worker.MAX_TEST_BYTES)):
        if type(text) is not str:
            return label + ":not_text"
        try:
            size = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            return label + ":encoding"
        if size > limit:
            return label + ":too_many_bytes"
    return None


def _read_regular(path, limit, label):
    """Read at most limit bytes from a regular, non-link file, exactly once."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise ForgeError(label + ":missing")
    except OSError:
        raise ForgeError(label + ":read_failed")
    if not stat.S_ISREG(st.st_mode) or getattr(st, "st_file_attributes", 0) & _REPARSE:
        raise ForgeError(label + ":not_regular")
    try:
        with open(path, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise ForgeError(label + ":not_regular")
            data = f.read(limit + 1)
    except OSError:
        raise ForgeError(label + ":read_failed")
    if len(data) > limit:
        raise ForgeError(label + ":too_large")
    return data


def _parse_manifest(data):
    """Return {name: entry} from manifest bytes or raise ForgeError."""
    try:
        doc = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates,
                         parse_constant=_reject_constant)
    except _DuplicateKey:
        raise ForgeError("manifest:duplicate_key")
    except (ValueError, RecursionError):
        raise ForgeError("manifest:invalid_json")
    if type(doc) is not dict or set(doc) != MANIFEST_KEYS:
        raise ForgeError("manifest:schema")
    if type(doc["version"]) is not int or doc["version"] != MANIFEST_VERSION:
        raise ForgeError("manifest:unknown_version")
    if type(doc["policy"]) is not str or doc["policy"] != POLICY_VERSION:
        raise ForgeError("manifest:unknown_policy")
    tools = doc["tools"]
    if type(tools) is not list:
        raise ForgeError("manifest:schema")
    if len(tools) > MAX_TOOLS:
        raise ForgeError("manifest:too_many_tools")
    entries, folded = {}, set()
    for item in tools:
        if type(item) is not dict or set(item) != ENTRY_KEYS:
            raise ForgeError("manifest:entry_schema")
        name = item["name"]
        reason = _precheck(name, item["description"], "", item["test"])
        if reason:
            raise ForgeError("manifest:" + reason)
        if name.lower() in folded:  # case-insensitive filesystems share one file
            raise ForgeError("manifest:duplicate_name")
        folded.add(name.lower())
        for key in ("code_sha256", "test_sha256"):
            if type(item[key]) is not str or not SHA256_RE.fullmatch(item[key]):
                raise ForgeError("manifest:hash_invalid")
        if _sha256(item["test"].encode("utf-8")) != item["test_sha256"]:
            raise ForgeError("manifest:test_hash_mismatch")
        entries[name] = item
    return entries


def _encode_manifest(entries):
    if len(entries) > MAX_TOOLS:
        raise ForgeError("manifest:too_many_tools")
    doc = {"version": MANIFEST_VERSION, "policy": POLICY_VERSION,
           "tools": [entries[n] for n in sorted(entries)]}
    data = json.dumps(doc, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      indent=1).encode("ascii")
    if len(data) > MAX_MANIFEST_BYTES:
        raise ForgeError("manifest:too_large")
    return data


class _ToolProxy:
    """Host-side callable. Each call is a child-process RPC to worker.py;
    the generated code is never executed in the host."""

    def __init__(self, forge, name, code):
        self._forge = forge
        self._name = name
        self._code = code  # the exact source that passed the tests

    def __call__(self, *args):
        args = list(args)
        try:
            check_value(args)
        except ForgeError as e:
            raise ToolInvocationError(_sanitize(e.code))
        resp = self._forge._run_worker(
            {"op": "invoke", "name": self._name, "code": self._code, "args": args})
        if not resp["ok"]:
            raise ToolInvocationError(_sanitize(resp.get("error")))
        try:
            return check_value(resp.get("value"))
        except ForgeError as e:
            raise ToolInvocationError(_sanitize(e.code))

    def __repr__(self):
        return "<forged tool %s>" % self._name


class Forge:
    def __init__(self, tool_dir, mode="os"):
        if mode not in MODES:
            raise ValueError("mode must be 'os' or 'restricted'")
        if not os.path.isdir(tool_dir):
            raise ValueError("tool_dir must be an existing directory")
        self.tool_dir = os.path.abspath(tool_dir)
        self.mode = mode
        self.TOOLS = {}  # name -> (description, callable proxy)
        self.restore_errors = []  # [{"name": str or None, "reason": str}]
        self._manifest_path = os.path.join(self.tool_dir, MANIFEST_NAME)
        self._entries = {}  # name -> manifest entry, as last written/loaded
        self._manifest_ok = True
        if mode == "restricted":
            try:
                self._restore()
            except Exception:
                self._manifest_ok = False
                self._restore_error(None, "restore:internal_error")

    def forge_tool(self, name, description, code, test):
        """Return "registered:<name>" or "testfailed:<reason>"."""
        try:
            return self._forge_tool(name, description, code, test)
        except Exception:
            return "testfailed:internal_error"

    def _forge_tool(self, name, description, code, test):
        if self.mode == "os":
            return "testfailed:os_sandbox_unavailable"
        reason = _precheck(name, description, code, test)
        if reason:
            return "testfailed:" + _sanitize(reason)
        if not self._manifest_ok:
            return "testfailed:manifest_invalid"
        if name not in self._entries:
            if any(n.lower() == name.lower() for n in self._entries):
                return "testfailed:name:case_conflict"
            if os.path.lexists(self._tool_path(name)):
                return "testfailed:name:exists_unregistered"
        resp = self._run_worker({"op": "test", "name": name, "code": code, "test": test})
        if not resp["ok"]:
            return "testfailed:" + _sanitize(resp.get("error"))
        source = code.encode("utf-8")
        entries = dict(self._entries)
        entries[name] = {"name": name, "description": description,
                         "code_sha256": _sha256(source), "test": test,
                         "test_sha256": _sha256(test.encode("utf-8"))}
        try:
            self._publish(name, source, _encode_manifest(entries))
        except ForgeError as e:
            return "testfailed:" + _sanitize(e.code)
        except (OSError, ValueError):
            return "testfailed:publish_failed"
        self._entries = entries
        self.TOOLS[name] = (description, _ToolProxy(self, name, code))
        return "registered:" + name

    # ------------------------------------------------------------ persistence

    def _tool_path(self, name):
        target = os.path.join(self.tool_dir, name + ".py")
        if os.path.dirname(os.path.abspath(target)) != self.tool_dir:
            raise ForgeError("name:path_escape")
        return target

    def _stage(self, data):
        fd, tmp = tempfile.mkstemp(dir=self.tool_dir, prefix=".forge-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            _unlink(tmp)
            raise
        return tmp

    def _publish(self, name, source, manifest):
        """Replace source then manifest; move the old source back on failure."""
        target = self._tool_path(name)
        src_tmp = self._stage(source)
        try:
            man_tmp = self._stage(manifest)
        except BaseException:
            _unlink(src_tmp)
            raise
        reserved = backup = None
        moved = False
        try:
            if os.path.lexists(target):
                fd, reserved = tempfile.mkstemp(dir=self.tool_dir, prefix=".forge-",
                                                suffix=".bak")
                os.close(fd)
                os.replace(target, reserved)
                backup, reserved = reserved, None
            os.replace(src_tmp, target)
            moved = True
            os.replace(man_tmp, self._manifest_path)
        except BaseException:
            for path in (src_tmp, man_tmp, reserved):
                if path:
                    _unlink(path)
            try:
                if backup:
                    os.replace(backup, target)
                elif moved:
                    os.unlink(target)
            except OSError:
                pass  # leftover state is a digest mismatch: restore fails closed
            raise
        if backup:
            _unlink(backup)

    def _restore_error(self, name, reason):
        if len(self.restore_errors) < MAX_RESTORE_ERRORS:
            self.restore_errors.append({"name": name, "reason": _sanitize(reason)})

    def _restore(self):
        if not os.path.lexists(self._manifest_path):
            return
        try:
            self._entries = _parse_manifest(
                _read_regular(self._manifest_path, MAX_MANIFEST_BYTES, "manifest"))
        except ForgeError as e:
            self._manifest_ok = False
            self._restore_error(None, e.code)
            return
        for name, entry in self._entries.items():
            try:
                code = self._restore_one(name, entry)
            except ForgeError as e:
                self._restore_error(name, e.code)
                continue
            self.TOOLS[name] = (entry["description"], _ToolProxy(self, name, code))

    def _restore_one(self, name, entry):
        data = _read_regular(self._tool_path(name), _worker.MAX_SOURCE_BYTES, "file")
        if _sha256(data) != entry["code_sha256"]:
            raise ForgeError("file:hash_mismatch")
        try:
            code = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ForgeError("file:encoding")
        reason = _precheck(name, entry["description"], code, entry["test"])
        if reason:
            raise ForgeError(reason)
        resp = self._run_worker({"op": "test", "name": name, "code": code,
                                 "test": entry["test"]})
        if not resp["ok"]:
            raise ForgeError("retest:" + str(resp.get("error")))
        return code

    # ----------------------------------------------------------------- worker

    def _run_worker(self, request):
        """Run one request in a fresh child; always returns {"ok": bool, ...}."""
        try:
            payload = json.dumps(request, ensure_ascii=True, allow_nan=False).encode("ascii")
        except (TypeError, ValueError, RecursionError):
            return {"ok": False, "error": "request:not_json"}
        if len(payload) > _worker.MAX_REQUEST_BYTES:
            return {"ok": False, "error": "request:too_large"}
        try:
            work = tempfile.mkdtemp(dir=self.tool_dir, prefix=".forge-run-")
        except OSError:
            return {"ok": False, "error": "worker:workdir_failed"}
        env = {"TEMP": work, "TMP": work}
        for key in ("SystemRoot", "WINDIR"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        try:
            # run() kills and waits for the child on timeout.
            proc = subprocess.run(
                [sys.executable, "-I", "-S", WORKER_PATH],
                input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=work, env=env, timeout=WALL_TIMEOUT_SECONDS, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout:wall"}
        except (OSError, ValueError, subprocess.SubprocessError):
            return {"ok": False, "error": "worker:spawn_failed"}
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if proc.returncode != 0:
            return {"ok": False, "error": "worker:exit_nonzero"}
        if len(proc.stdout) > MAX_RESPONSE_BYTES:
            return {"ok": False, "error": "worker:response_too_large"}
        try:
            resp = json.loads(proc.stdout.decode("ascii"), parse_constant=_reject_constant)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return {"ok": False, "error": "worker:bad_response"}
        if type(resp) is not dict or type(resp.get("ok")) is not bool:
            return {"ok": False, "error": "worker:bad_response"}
        return resp
