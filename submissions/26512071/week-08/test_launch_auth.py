"""RED test: launch_market_runtime.ps1 must refresh OPENAI_API_KEY from the User environment at launch time.

Run (from the repo root, repo venv):
    .venv\\Scripts\\python.exe -m unittest discover -s submissions/26512071/week-08 -p test_launch_auth.py -v

Runs the real launcher in real Windows PowerShell, up to (not into) the supervisor: $HOME points at a temporary
fixture, so the trusted rejoin.ps1, the venv python and market_runtime.py it reaches are fixtures. The fixture
market_runtime.py only records which fixture value it inherited, as a label. The User-scope lookup is faked by
shadowing Get-ItemProperty / Get-ItemPropertyValue for HKCU:\\Environment; the real registry, the real trusted
launcher and real credentials are never read. Key values are random sentinels and are never printed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAUNCHER = HERE / "launch_market_runtime.ps1"
WINDOWS = os.name == "nt"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
IN_VENV = sys.prefix != sys.base_prefix
# Reads the fake cannot intercept; if the launcher used one, running it here would touch the real User key.
UNFAKEABLE_USER_READS = ("GetEnvironmentVariable", "Microsoft.Win32", "reg query", "reg.exe", "Get-CimInstance",
                         "Get-WmiObject")

PROBE = r'''import json, os, sys
from pathlib import Path
here = Path(__file__).resolve().parent
labels = json.loads((here / "labels.json").read_text(encoding="utf-8"))
key = os.environ.get("OPENAI_API_KEY")
seen = "absent" if key is None else "empty" if key == "" else next((k for k, v in labels.items() if v == key), "other")
(here / "probe-result.json").write_text(json.dumps({"key": seen, "owner": os.environ.get("MARKET_OWNER") == labels["owner"],
                                                    "argv": sys.argv[1:]}), encoding="utf-8")
'''

WRAPPER = r'''$ErrorActionPreference = 'Stop'
$global:FakeUser = {fake_user}
$global:FakeLookups = {lookups}
function global:Test-FakeUserEnv($p) {{
    "$p" -match '^(HKCU:|(Microsoft\.PowerShell\.Core\\)?Registry::HKEY_CURRENT_USER)\\Environment\\?$'
}}
function global:Get-ItemProperty {{
    [CmdletBinding()]
    param([Parameter(Position=0)][Alias('LiteralPath','PSPath')][string[]]$Path, [Parameter(Position=1)][string[]]$Name)
    if (-not (Test-FakeUserEnv $Path)) {{ return Microsoft.PowerShell.Management\Get-ItemProperty @PSBoundParameters }}
    Add-Content -LiteralPath $global:FakeLookups 'Get-ItemProperty'
    if ($null -ne $global:FakeUser) {{ return [pscustomobject]@{{ OPENAI_API_KEY = $global:FakeUser }} }}
    if ($Name) {{ Write-Error -Message 'Property does not exist at path HKCU:\Environment.' -Category InvalidArgument }}
    else {{ return [pscustomobject]@{{}} }}
}}
function global:Get-ItemPropertyValue {{
    [CmdletBinding()]
    param([Parameter(Position=0)][Alias('LiteralPath','PSPath')][string[]]$Path, [Parameter(Position=1)][string[]]$Name)
    if (-not (Test-FakeUserEnv $Path)) {{ return Microsoft.PowerShell.Management\Get-ItemPropertyValue @PSBoundParameters }}
    Add-Content -LiteralPath $global:FakeLookups 'Get-ItemPropertyValue'
    if ($null -ne $global:FakeUser) {{ return $global:FakeUser }}
    Write-Error -Message 'Property does not exist at path HKCU:\Environment.' -Category InvalidArgument
}}
Set-Variable -Name HOME -Value {home} -Scope Global -Force
if ($HOME -ne {home}) {{ exit 97 }}      # never let the launcher reach the real trusted rejoin.ps1 or repo
& {launcher}
exit $LASTEXITCODE
'''


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(WINDOWS and POWERSHELL.exists(), "Windows PowerShell launcher")
@unittest.skipUnless(IN_VENV, "needs a venv python launcher to copy as the fixture .venv python")
class LaunchUserKeyRefreshTests(unittest.TestCase):
    def setUp(self):
        src = LAUNCHER.read_text(encoding="utf-8")
        for bad in UNFAKEABLE_USER_READS:
            # checked before any launch: a hit would read the real credential instead of the fixture
            self.assertNotIn(bad, src, "User scope must be read through Get-ItemProperty(Value) HKCU:\\Environment")

    def _launch(self, inherited, user):
        """Run the real launcher against a fixture home; return (probe label record, user lookups)."""
        labels = {"stale": "fixture-stale-" + uuid.uuid4().hex, "updated": "fixture-user-" + uuid.uuid4().hex,
                  "owner": "fixture-owner-" + uuid.uuid4().hex}
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            home = root / "home"
            (home / "agent-market").mkdir(parents=True)
            (home / "agent-market" / "rejoin.ps1").write_text(
                f"# fixture trusted launcher\n$env:MARKET_OWNER = '{labels['owner']}'\nWrite-Output 'not imported'\n",
                encoding="ascii")
            repo = home / "ai-agent-engineering-101"
            scripts = repo / ".venv" / "Scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(sys.executable, scripts / "python.exe")
            shutil.copy2(Path(sys.prefix) / "pyvenv.cfg", repo / ".venv" / "pyvenv.cfg")
            week = repo / "submissions" / "26512071" / "week-08"
            week.mkdir(parents=True)
            (week / "market_runtime.py").write_text(PROBE, encoding="utf-8")
            (week / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
            lookups = root / "user-lookups.txt"
            fake_user = "$null" if user is None else ps_quote(labels.get(user, user))
            wrapper = root / "wrapper.ps1"
            wrapper.write_text(WRAPPER.format(fake_user=fake_user, lookups=ps_quote(lookups), home=ps_quote(home),
                                              launcher=ps_quote(LAUNCHER)), encoding="ascii")

            env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "MARKET_OWNER")}
            if inherited is not None:
                env["OPENAI_API_KEY"] = labels[inherited]          # what a stale Scheduler environment hands over
            r = subprocess.run([str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                                "Bypass", "-File", str(wrapper)], env=env, cwd=str(root), capture_output=True,
                               timeout=90)
            printed = r.stdout + r.stderr
            for name in ("stale", "updated", "owner"):
                self.assertFalse(labels[name].encode() in printed, f"launch printed the {name} fixture value")
            self.assertEqual(r.returncode, 0, f"launch prefix failed (exit {r.returncode})")
            result = week / "probe-result.json"
            self.assertTrue(result.exists(), "fixture supervisor was not reached")
            record = json.loads(result.read_text(encoding="utf-8"))
            used = lookups.read_text(encoding="utf-8").split() if lookups.exists() else []
        self.assertTrue(record["owner"], "trusted launcher $env: lines were not imported first")
        self.assertEqual(record["argv"][:2], ["--exit-with-parent", "--parent-pid"])
        self.assertEqual(len(record["argv"]), 3)
        self.assertGreater(int(record["argv"][2]), 0)
        return record, used

    def test_user_key_refresh_at_launch(self):
        with self.subTest("updated User value replaces stale inherited key"):
            record, used = self._launch(inherited="stale", user="updated")
            self.assertTrue(used, "launcher never looked up the User-scope OPENAI_API_KEY")
            self.assertEqual(record["key"], "updated")
        with self.subTest("User value reaches a launch with no inherited key"):
            record, _ = self._launch(inherited=None, user="updated")
            self.assertEqual(record["key"], "updated")
        with self.subTest("missing User value keeps the explicit inherited key"):
            record, _ = self._launch(inherited="stale", user=None)
            self.assertEqual(record["key"], "stale")
        with self.subTest("empty User value keeps the explicit inherited key"):
            record, _ = self._launch(inherited="stale", user="")
            self.assertEqual(record["key"], "stale")


if __name__ == "__main__":
    unittest.main()
