"""Week10 tool forge — tracer test (RED stage).

Educational core: an agent writes a tool, the forge runs the tool's own test,
and only a passing tool is published to tool_dir and registered in TOOLS.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

try:
    import forge as forge_mod
    from forge import Forge, ToolInvocationError
except ImportError:
    forge_mod = Forge = ToolInvocationError = None

HERE = os.path.dirname(os.path.abspath(__file__))

BAD_ADD = "def add(a, b):\n    return a - b\n"
GOOD_ADD = "def add(a, b):\n    return a + b\n"
ADD_TEST = "assert add(2, 3) == 5\n"
DESC = "Add two numbers."


class ForgeTests(unittest.TestCase):
    def test_retry_register_invoke(self):
        self.assertIsNotNone(Forge, "Forge implementation missing")

        with tempfile.TemporaryDirectory(dir=HERE) as tool_dir:
            forge = Forge(tool_dir, mode="restricted")
            published = os.path.join(tool_dir, "add.py")

            # First attempt: buggy tool must fail its own test and stay unpublished.
            result = forge.forge_tool("add", "Add two numbers.", BAD_ADD, ADD_TEST)
            self.assertTrue(result.startswith("testfailed:"), result)
            self.assertNotIn("add", forge.TOOLS)
            self.assertFalse(os.path.exists(published))

            # Retry: corrected tool registers, publishes, and is invocable.
            result = forge.forge_tool("add", "Add two numbers.", GOOD_ADD, ADD_TEST)
            self.assertEqual(result, "registered:add")
            self.assertIn("add", forge.TOOLS)
            description, fn = forge.TOOLS["add"]
            self.assertEqual(description, "Add two numbers.")
            self.assertEqual(fn(2, 3), 5)
            self.assertTrue(os.path.exists(published))

    def test_restart_restore_and_tamper(self):
        self.assertIsNotNone(Forge, "Forge implementation missing")

        with tempfile.TemporaryDirectory(dir=HERE) as tool_dir:
            forge = Forge(tool_dir, mode="restricted")
            result = forge.forge_tool("add", "Add two numbers.", GOOD_ADD, ADD_TEST)
            self.assertEqual(result, "registered:add")

            # Unrelated file dropped into tool_dir: must never be imported or
            # auto-discovered. Its side effect would create the marker.
            marker = os.path.join(tool_dir, "orphan-ran.marker")
            with open(os.path.join(tool_dir, "orphan.py"), "w", encoding="utf-8") as f:
                f.write("open(%r, 'w').write('x')\n"
                        "def orphan():\n    return 1\n" % marker)

            # Restart: a fresh interpreter rebuilds TOOLS from tool_dir alone.
            child = (
                "import json, sys\n"
                "sys.path.insert(0, %r)\n"
                "from forge import Forge\n"
                "forge = Forge(sys.argv[1], mode='restricted')\n"
                "print(json.dumps({'result': forge.TOOLS['add'][1](4, 5),\n"
                "                  'orphan': 'orphan' in forge.TOOLS}))\n" % HERE)
            proc = subprocess.run(
                [sys.executable, "-c", child, tool_dir],
                cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=60, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
            out = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
            self.assertEqual(out["result"], 9)
            self.assertFalse(out["orphan"])
            self.assertFalse(os.path.exists(marker))

            # Tamper with the published record after restart: restore must
            # refuse it and report why.
            with open(os.path.join(tool_dir, "add.py"), "w", encoding="utf-8") as f:
                f.write("def add(a, b):\n    return a * b\n")
            tampered = Forge(tool_dir, mode="restricted")
            self.assertNotIn("add", tampered.TOOLS)
            self.assertNotIn("orphan", tampered.TOOLS)
            self.assertTrue(getattr(tampered, "restore_errors", None))
            self.assertFalse(os.path.exists(marker))

    def test_test_must_execute_tool(self):
        # RED: the test mentions f(0) but `or` short-circuits, so the tool is
        # never invoked. A static Call-presence check alone wrongly accepts it.
        self.assertIsNotNone(Forge, "Forge implementation missing")

        with tempfile.TemporaryDirectory(dir=HERE) as tool_dir:
            forge = Forge(tool_dir, mode="restricted")
            result = forge.forge_tool("f", "Reciprocal.", "def f(x):\n    return 1/x\n",
                                      "assert True or f(0)\n")
            self.assertEqual(result, "testfailed:test:must_execute_tool")
            self.assertNotIn("f", forge.TOOLS)
            self.assertFalse(os.path.exists(os.path.join(tool_dir, "f.py")))
            self.assertFalse(os.path.exists(os.path.join(tool_dir, "manifest.json")))


# ---------------------------------------------------------------------------
# Regression tests for safety behaviour that is already implemented. These
# are expected to pass against the current forge.py/worker.py (not RED).
# ---------------------------------------------------------------------------

def _sha(text_or_bytes):
    data = text_or_bytes.encode("utf-8") if type(text_or_bytes) is str else text_or_bytes
    return hashlib.sha256(data).hexdigest()


def _entry(name="add", code=GOOD_ADD, test=ADD_TEST, description=DESC):
    return {"name": name, "description": description, "code_sha256": _sha(code),
            "test": test, "test_sha256": _sha(test)}


def _manifest(tools, version=1, policy="restricted-v1"):
    return json.dumps({"version": version, "policy": policy, "tools": tools}).encode("ascii")


def _with_digest(manifest, digest):
    doc = json.loads(manifest)
    doc["tools"][0]["code_sha256"] = digest
    return json.dumps(doc).encode("ascii")


class _ForgeCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Forge, "Forge implementation missing")
        tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(tmp.cleanup)
        self.tool_dir = tmp.name

    def path(self, name):
        return os.path.join(self.tool_dir, name)

    def write(self, name, data):
        with open(self.path(name), "wb") as f:
            f.write(data)

    def read(self, name):
        with open(self.path(name), "rb") as f:
            return f.read()

    def new_forge(self):
        return Forge(self.tool_dir, mode="restricted")

    def assert_unpublished(self, forge, name):
        self.assertNotIn(name, forge.TOOLS)
        self.assertFalse(os.path.lexists(self.path(name + ".py")))
        self.assertFalse(os.path.lexists(self.path("manifest.json")))


class RejectedInputTests(_ForgeCase):
    def test_rejected_names(self):
        # Host-side precheck: no child is spawned, so tool_dir stays empty.
        forge = self.new_forge()
        for name in ["../evil", "a/b", "a\\b", "add.py", "", "CON", "con", "com1",
                     "LPT9", "nul", "_add", "class", "True", "len", "a" * 49,
                     "\u00e4dd", 5, None]:
            with self.subTest(name=name):
                self.assertEqual(forge.forge_tool(name, DESC, GOOD_ADD, ADD_TEST),
                                 "testfailed:name:invalid")
                self.assertEqual(forge.TOOLS, {})
                self.assertEqual(os.listdir(self.tool_dir), [])

    def test_rejected_sizes_and_text(self):
        forge = self.new_forge()
        cases = [
            ("code_ascii_bytes", DESC, GOOD_ADD + "#" * 4096, ADD_TEST, "code:too_many_bytes"),
            # 1400 chars but 4200 UTF-8 bytes: the limit is bytes, not chars.
            ("code_utf8_bytes", DESC, GOOD_ADD + "# " + "\uac00" * 1400, ADD_TEST,
             "code:too_many_bytes"),
            ("test_bytes", DESC, GOOD_ADD, ADD_TEST + "#" * 4096, "test:too_many_bytes"),
            ("code_surrogate", DESC, GOOD_ADD + "#\ud800", ADD_TEST, "code:encoding"),
            ("code_not_text", DESC, None, ADD_TEST, "code:not_text"),
            ("test_not_text", DESC, GOOD_ADD, b"assert add(2, 3) == 5", "test:not_text"),
            ("description_empty", "", GOOD_ADD, ADD_TEST, "description:invalid"),
            ("description_long", "x" * 257, GOOD_ADD, ADD_TEST, "description:invalid"),
            ("description_control", "a\nb", GOOD_ADD, ADD_TEST, "description:invalid"),
        ]
        for label, description, code, test, reason in cases:
            with self.subTest(label):
                self.assertEqual(forge.forge_tool("add", description, code, test),
                                 "testfailed:" + reason)
                self.assertEqual(os.listdir(self.tool_dir), [])

    def test_denied_code_and_test(self):
        # Rejected by the worker's static allow-list before any evaluation.
        def body(expr):
            return "def add(a, b):\n    return %s\n" % expr

        cases = [
            ("attribute", body("a.real"), ADD_TEST, "ast:node_denied:attribute"),
            ("reflection", body("a.__class__"), ADD_TEST, "ast:node_denied:attribute"),
            ("getattr", body("getattr(a, 'real')"), ADD_TEST, "ast:call_denied"),
            ("dunder_import", body("__import__('os')"), ADD_TEST, "ast:call_denied"),
            ("top_import", "import os\n" + GOOD_ADD, ADD_TEST, "code:need_exactly_one_def"),
            ("body_import", "def add(a, b):\n    import os\n    return a + b\n", ADD_TEST,
             "code:body_must_be_single_return"),
            ("exec", body("exec('a')"), ADD_TEST, "ast:call_denied"),
            ("eval", body("eval('a')"), ADD_TEST, "ast:call_denied"),
            ("open", body("open('x', 'w')"), ADD_TEST, "ast:call_denied"),
            ("subprocess", body("subprocess.run(['x'])"), ADD_TEST, "ast:call_denied"),
            ("while", "def add(a, b):\n    while a:\n        a = a - 1\n    return b\n",
             ADD_TEST, "code:body_must_be_single_return"),
            ("lambda_value", body("lambda: a"), ADD_TEST, "ast:node_denied:lambda"),
            ("lambda_call", body("(lambda: a)()"), ADD_TEST, "ast:call_denied"),
            ("recursive", body("add(a, b)"), ADD_TEST, "ast:call_denied"),
            ("listcomp", body("[x for x in a]"), ADD_TEST, "ast:node_denied:listcomp"),
            ("slice", body("a[0:1]"), ADD_TEST, "ast:node_denied:slice"),
            ("walrus", body("(c := a)"), ADD_TEST, "ast:node_denied:namedexpr"),
            ("unknown_name", body("a + c"), ADD_TEST, "ast:name_denied"),
            ("decorator", "@staticmethod\n" + GOOD_ADD, ADD_TEST, "code:def_extras_denied"),
            ("default_arg", "def add(a, b=1):\n    return a + b\n", ADD_TEST,
             "code:signature_denied"),
            ("varargs", "def add(*a):\n    return a\n", ADD_TEST, "code:signature_denied"),
            ("async", "async " + GOOD_ADD, ADD_TEST, "code:need_exactly_one_def"),
            ("syntax", "def add(a, b) return", ADD_TEST, "code:syntax_error"),
            ("nul_byte", GOOD_ADD + "\x00", ADD_TEST, "code:nul_byte"),
            ("name_mismatch", "def plus(a, b):\n    return a + b\n", ADD_TEST,
             "code:name_mismatch"),
            ("duplicate_arg", "def add(a, a):\n    return a\n", ADD_TEST, "code:duplicate_arg"),
            ("builtin_arg_name", "def add(a, len):\n    return a\n", ADD_TEST,
             "code:arg_name_invalid"),
            ("too_many_args", "def add(a, b, c, d, e, f, g, h, i):\n    return a\n", ADD_TEST,
             "code:too_many_args"),
            ("code_nodes", body("[" + "a, " * 250 + "]"), ADD_TEST, "code:too_many_nodes"),
            ("code_depth", body("-" * 40 + "a"), ADD_TEST, "ast:too_deep"),
            ("test_import", GOOD_ADD, "import os\n" + ADD_TEST, "test:only_plain_assert_allowed"),
            ("test_assert_msg", GOOD_ADD, "assert add(2, 3) == 5, 'm'\n",
             "test:only_plain_assert_allowed"),
            ("test_attribute", GOOD_ADD, "assert add(2, 3).real == 5\n",
             "ast:node_denied:attribute"),
            ("test_no_call", GOOD_ADD, "assert 1 == 1\n", "test:must_call_tool"),
            ("test_nodes", GOOD_ADD, "assert [" + "1, " * 450 + "] != add(2, 3)\n",
             "test:too_many_nodes"),
            ("test_assert_count", GOOD_ADD, "assert add(1, 1) == 2\n" * 33, "test:assert_count"),
        ]
        forge = self.new_forge()
        for label, code, test, reason in cases:
            with self.subTest(label):
                self.assertEqual(forge.forge_tool("add", DESC, code, test),
                                 "testfailed:" + reason)
                self.assert_unpublished(forge, "add")


CALC = ("def calc(op, a, b):\n"
        "    return a + b if op == 'add' else (a * b if op == 'mul' else a ** b)\n")
CALC_TEST = ("assert calc('add', 2, 3) == 5\n"
             "assert calc('mul', 2, 3) == 6\n"
             "assert calc('pow', 2, 3) == 8\n")


class InvocationLimitTests(_ForgeCase):
    def test_invocation_limits(self):
        forge = self.new_forge()
        self.assertEqual(forge.forge_tool("calc", "Calculator.", CALC, CALC_TEST),
                         "registered:calc")
        fn = forge.TOOLS["calc"][1]
        self.assertEqual(fn("add", 2, 3), 5)
        cases = [
            # Rejected host-side by check_value before any child is spawned.
            (("add", float("nan"), 1), "value:float_not_finite"),
            (("add", float("inf"), 1), "value:float_not_finite"),
            (("add", 2 ** 300, 1), "value:int_too_large"),
            (("add", "x" * 1025, ""), "value:str_too_long"),
            # Fixture correction (GREEN-03, not a limit change): [1] * 200 hit
            # the 16384-byte estimate (80 per int) before the 256-item cap.
            # None costs 5, so [None] * 257 isolates value:too_many_items.
            (("add", [None] * 257, 1), "value:too_many_items"),
            (("add", object(), 1), "value:unsupported_type"),
            (("add", (1,), 1), "value:unsupported_type"),
            (("add", b"x", 1), "value:unsupported_type"),
            # Rejected by the worker while interpreting.
            (("add", 1), "call:arity"),
            (("add", 1, 2, 3), "call:arity"),
            (("add", "x", 1), "eval:type_error"),
            (("add", "x" * 600, "y" * 600), "limit:str_len"),
            (("mul", "x", 2000), "limit:repeat"),
            (("mul", [1], 300), "limit:repeat"),
            (("mul", 2 ** 200, 2 ** 200), "limit:int_bits"),
            (("pow", 2, 10 ** 6), "limit:int_bits"),
            (("pow", 10, 100), "limit:int_bits"),
            (("pow", 10.0, 400), "eval:overflow"),
            (("pow", 0, -1), "eval:zero_division"),
        ]
        for args, reason in cases:
            with self.subTest(args=repr(args)[:60]):
                with self.assertRaises(ToolInvocationError) as cm:
                    fn(*args)
                self.assertEqual(str(cm.exception), reason)
        self.assertIn("calc", forge.TOOLS)

    def test_wall_timeout_kills_child(self):
        # Exercises only the wall-clock watchdog: subprocess.run's real
        # timeout -> kill -> wait path. It does NOT test CPU exhaustion
        # (that is the worker's step budget) or OS memory limits (none exist).
        forge = self.new_forge()
        self.assertEqual(forge.forge_tool("add", DESC, GOOD_ADD, ADD_TEST), "registered:add")
        fn = forge.TOOLS["add"][1]
        original = forge_mod.WALL_TIMEOUT_SECONDS
        forge_mod.WALL_TIMEOUT_SECONDS = 1e-9
        try:
            result = forge.forge_tool("calc", "Calculator.", CALC, CALC_TEST)
            with self.assertRaises(ToolInvocationError) as cm:
                fn(2, 3)
        finally:
            forge_mod.WALL_TIMEOUT_SECONDS = original
        self.assertEqual(result, "testfailed:timeout:wall")
        self.assertEqual(str(cm.exception), "timeout:wall")
        self.assertNotIn("calc", forge.TOOLS)
        self.assertFalse(os.path.lexists(self.path("calc.py")))
        self.assertEqual(fn(2, 3), 5)


class PublicationTests(_ForgeCase):
    def test_bad_replacement_preserves_prior(self):
        forge = self.new_forge()
        self.assertEqual(forge.forge_tool("add", DESC, GOOD_ADD, ADD_TEST), "registered:add")
        before = forge.TOOLS["add"]
        source, manifest = self.read("add.py"), self.read("manifest.json")
        cases = [
            (BAD_ADD, ADD_TEST, "assertion_failed:1"),
            ("def add(a, b):\n    return a.real\n", ADD_TEST, "ast:node_denied:attribute"),
            (GOOD_ADD, "assert add(2, 3) == 5, 'm'\n", "test:only_plain_assert_allowed"),
        ]
        for code, test, reason in cases:
            with self.subTest(reason):
                self.assertEqual(forge.forge_tool("add", "Replaced.", code, test),
                                 "testfailed:" + reason)
                self.assertIs(forge.TOOLS["add"], before)
                self.assertEqual(self.read("add.py"), source)
                self.assertEqual(self.read("manifest.json"), manifest)
        self.assertEqual(before[0], DESC)
        self.assertEqual(before[1](2, 3), 5)
        restarted = self.new_forge()
        self.assertEqual(restarted.restore_errors, [])
        self.assertEqual(restarted.TOOLS["add"][0], DESC)

    def test_orphan_name_not_overwritten(self):
        self.write("add.py", b"user file, not a tool\n")
        forge = self.new_forge()
        self.assertEqual(forge.restore_errors, [])
        self.assertEqual(forge.forge_tool("add", DESC, GOOD_ADD, ADD_TEST),
                         "testfailed:name:exists_unregistered")
        self.assertNotIn("add", forge.TOOLS)
        self.assertEqual(self.read("add.py"), b"user file, not a tool\n")
        self.assertFalse(os.path.lexists(self.path("manifest.json")))

    def test_case_conflict_refused(self):
        forge = self.new_forge()
        self.assertEqual(forge.forge_tool("add", DESC, GOOD_ADD, ADD_TEST), "registered:add")
        source = self.read("add.py")
        self.assertEqual(
            forge.forge_tool("ADD", DESC, "def ADD(a, b):\n    return a * b\n",
                             "assert ADD(2, 3) == 6\n"),
            "testfailed:name:case_conflict")
        self.assertNotIn("ADD", forge.TOOLS)
        self.assertEqual(self.read("add.py"), source)

    def test_proxy_holds_accepted_bytes(self):
        forge = self.new_forge()
        self.assertEqual(forge.forge_tool("add", DESC, GOOD_ADD, ADD_TEST), "registered:add")
        fn = forge.TOOLS["add"][1]
        self.write("add.py", b"def add(a, b):\n    return a * b\n")
        self.assertEqual(fn(2, 3), 5)
        os.unlink(self.path("add.py"))
        self.assertEqual(fn(2, 3), 5)
        restarted = self.new_forge()
        self.assertNotIn("add", restarted.TOOLS)
        self.assertEqual(restarted.restore_errors, [{"name": "add", "reason": "file:missing"}])


class ManifestTests(_ForgeCase):
    def test_invalid_manifest_restores_nothing_and_blocks(self):
        def with_entry(**changes):
            entry = _entry()
            entry.update(changes)
            return _manifest([entry])

        too_many = [_entry(name="t%d" % i, code="def t%d():\n    return 1\n" % i,
                           test="assert t%d() == 1\n" % i) for i in range(65)]
        extra_top = json.loads(_manifest([_entry()]))
        extra_top["extra"] = 1
        cases = [
            ("corrupt", b"{not json", "manifest:invalid_json"),
            ("not_utf8", b"\xff\xfe", "manifest:invalid_json"),
            ("nan", b'{"version": NaN, "policy": "restricted-v1", "tools": []}',
             "manifest:invalid_json"),
            ("not_object", b"[]", "manifest:schema"),
            ("extra_top_key", json.dumps(extra_top).encode("ascii"), "manifest:schema"),
            ("version_2", _manifest([], version=2), "manifest:unknown_version"),
            ("version_bool", _manifest([], version=True), "manifest:unknown_version"),
            ("version_str", _manifest([], version="1"), "manifest:unknown_version"),
            ("policy", _manifest([], policy="restricted-v0"), "manifest:unknown_policy"),
            ("duplicate_key",
             b'{"version": 1, "version": 1, "policy": "restricted-v1", "tools": []}',
             "manifest:duplicate_key"),
            ("entry_extra_key", with_entry(extra=1), "manifest:entry_schema"),
            ("entry_bad_name", with_entry(name="CON"), "manifest:name:invalid"),
            ("duplicate_name", _manifest([_entry(), _entry(name="ADD")]),
             "manifest:duplicate_name"),
            ("test_hash", with_entry(test="assert add(1, 1) == 2\n"),
             "manifest:test_hash_mismatch"),
            ("code_hash_short", with_entry(code_sha256="XYZ"), "manifest:hash_invalid"),
            ("code_hash_upper", with_entry(code_sha256=_sha(GOOD_ADD).upper()),
             "manifest:hash_invalid"),
            ("too_many_tools", _manifest(too_many), "manifest:too_many_tools"),
            ("too_large", b" " * (1024 * 1024 + 1), "manifest:too_large"),
        ]
        self.write("add.py", GOOD_ADD.encode("utf-8"))
        for label, data, reason in cases:
            with self.subTest(label):
                self.write("manifest.json", data)
                forge = self.new_forge()
                self.assertEqual(forge.TOOLS, {})
                self.assertEqual(forge.restore_errors, [{"name": None, "reason": reason}])
                self.assertEqual(forge.forge_tool("other", DESC,
                                                  "def other(a):\n    return a\n",
                                                  "assert other(1) == 1\n"),
                                 "testfailed:manifest_invalid")
                self.assertEqual(self.read("manifest.json"), data)
                self.assertFalse(os.path.lexists(self.path("other.py")))

    def test_restore_revalidates_source(self):
        # A matching digest is not trust: restore re-parses and re-runs the
        # stored test on the exact bytes, so code that was never accepted
        # (even with its digest rewritten into the manifest) is refused.
        marker = self.path("evil-ran.marker")
        cases = [
            ("digest_mismatch", GOOD_ADD.encode("utf-8"), _sha("other"), "file:hash_mismatch"),
            ("missing", None, _sha(GOOD_ADD), "file:missing"),
            ("not_utf8", b"\xff\xfe", _sha(b"\xff\xfe"), "file:encoding"),
            ("open_call", ("def add(a, b):\n    return open(%r, 'w')\n" % marker).encode("utf-8"),
             None, "retest:ast:call_denied"),
            ("import", ("import os\n" + GOOD_ADD).encode("utf-8"), None,
             "retest:code:need_exactly_one_def"),
            ("wrong_result", b"def add(a, b):\n    return a * b\n", None,
             "retest:assertion_failed:1"),
        ]
        for label, source, digest, reason in cases:
            with self.subTest(label):
                if os.path.lexists(self.path("add.py")):
                    os.unlink(self.path("add.py"))
                if source is not None:
                    self.write("add.py", source)
                manifest = _manifest([_entry(code=source if digest is None else GOOD_ADD)])
                if digest is not None:
                    manifest = _with_digest(manifest, digest)
                self.write("manifest.json", manifest)
                forge = self.new_forge()
                self.assertNotIn("add", forge.TOOLS)
                self.assertEqual(forge.restore_errors, [{"name": "add", "reason": reason}])
                self.assertEqual(self.read("manifest.json"), manifest)
                if source is not None:
                    self.assertEqual(self.read("add.py"), source)
                self.assertFalse(os.path.exists(marker))


if __name__ == "__main__":
    unittest.main()
