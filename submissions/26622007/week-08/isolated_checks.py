"""Offline checks in a disposable source copy; never launch a child or connect a socket.

Run with the approved Python and -B. With no arguments, run the regressions for
the stale-code guard and price OFF settings. This is not the full project suite:
that suite intentionally launches children, writes Git repositories and binds ports.
--meta-source accepts only the hash-pinned afc17a3 meta_harness.py fixture:
current tests are unchanged and only the disposable meta_harness.py is replaced.
This is a trusted historical source seam, not an arbitrary Python sandbox.
No Git is invoked.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parent
DEFAULTS = ("test_meta.StaleCodeTest", "test_price_trial", "test_profit_rules.PriceTest")
APPROVED_META_FIXTURE_SHA256 = "b952035894cf9fba4f3c1616ee12ab0441a5fcf9cc3c96941c6dd2ac01c8ee35"


def manifest(paths):
    """Only hashes/counts, including absent paths. Never parse protected contents."""
    digest, files, lines = hashlib.sha256(), 0, 0
    for root in paths:
        entries = sorted(root.rglob("*")) if root.is_dir() else [root]
        digest.update(os.fsencode(root))
        for path in entries:
            if path.is_symlink():
                digest.update(os.fsencode(path) + os.fsencode(os.readlink(path)))
            elif path.is_file():
                file_hash, count = hashlib.sha256(), 0
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(65536), b""):
                        file_hash.update(block)
                        count += block.count(b"\n")
                digest.update(os.fsencode(path) + file_hash.digest())
                files += 1
                lines += count
            else:
                digest.update(os.fsencode(path) + b":absent-or-directory")
    return digest.hexdigest(), files, lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meta-source", type=Path)
    parser.add_argument("--seller-source", type=Path)
    parser.add_argument("tests", nargs="*")
    options = parser.parse_args()
    names = options.tests or DEFAULTS
    # Do not accidentally run the Git/model integration classes in test_meta.
    if any(not (name == "test_price_trial" or name.startswith("test_price_trial.")
                or name == "test_profit_rules.PriceTest"
                or name.startswith("test_profit_rules.PriceTest.")
                or name == "test_meta.StaleCodeTest"
                or name.startswith("test_meta.StaleCodeTest.")
                or name == "test_sol_seller_margin"
                or name.startswith("test_sol_seller_margin.")) for name in names):
        raise SystemExit("Only the approved offline test seams are allowed")
    meta_bytes = None
    seller_bytes = None
    if options.seller_source is not None:
        fixture = options.seller_source.absolute()
        if fixture != fixture.resolve() or not stat.S_ISREG(fixture.lstat().st_mode) or fixture.stat().st_size > 2_000_000:
            parser.error('seller-source must be a canonical regular pinned fixture')
        seller_bytes = fixture.read_bytes()
        if hashlib.sha256(seller_bytes).hexdigest() != 'eb9f4f81bdd3d805c7fb747aff3416b22c8884e4f19823addf1ee421bff0d636':
            parser.error('seller-source must match the approved be49 historical seller')
    if options.meta_source is not None:
        fixture = options.meta_source.absolute()
        if (fixture != fixture.resolve() or fixture.suffix != ".py"
                or not stat.S_ISREG(fixture.lstat().st_mode)
                or fixture.stat().st_size > 2_000_000):
            parser.error("meta-source must be a canonical regular .py file of at most 2 MB")
        meta_bytes = fixture.read_bytes()
        if hashlib.sha256(meta_bytes).hexdigest() != APPROVED_META_FIXTURE_SHA256:
            parser.error("meta-source must match the approved afc17a3 historical source hash")
        try:
            compile(meta_bytes, "meta-source", "exec")
        except (SyntaxError, ValueError):
            parser.error("meta-source must contain syntactically valid Python")
    live = Path("/Users/noah/work/ai-agent-engineering-101/submissions/26622007/week-08")
    # Same fixed static guard for RED and GREEN. Never hash moving live ledgers:
    # natural changes there cannot establish whether this helper contaminated it.
    controls = ("pricing.json", "tools/quote_owner_executor.py",
                ".market/seller_concession_trial.json", ".market/seller_reader_trial.json",
                ".market/quote_hold/config.json")
    protected = [root / name for root in (SOURCE, live) for name in controls]
    protected += list(SOURCE.glob("*.py")) + list(live.glob("*.py"))
    protected += [Path.home() / p for p in (".codex/config.toml", ".claude/settings.json",
                                           ".claude-b/settings.json")]
    before = manifest(protected)
    original_cwd = Path.cwd()
    sys.dont_write_bytecode = True
    with tempfile.TemporaryDirectory(prefix="ax-offline-", dir="/tmp") as directory:
        root = Path(directory).resolve()
        copy = root / "source"
        copy.mkdir()
        for path in SOURCE.glob("*.py"):
            if path.is_symlink():
                raise RuntimeError("Source symlinks are not allowed")
            shutil.copyfile(path, copy / path.name)
        if meta_bytes is not None:
            (copy / "meta_harness.py").write_bytes(meta_bytes)
        if seller_bytes is not None:
            (copy / 'seller.py').write_bytes(seller_bytes)
            print('SELLER_SOURCE fixture=be49 sha256='+hashlib.sha256(seller_bytes).hexdigest(), flush=True)
        selected_meta = (copy / "meta_harness.py").read_bytes()
        print(f"META_SOURCE mode={'fixture' if meta_bytes is not None else 'current'} "
              f"sha256={hashlib.sha256(selected_meta).hexdigest()} "
              f"lines={selected_meta.count(bytes([10]))}; current_tests=unchanged", flush=True)
        shutil.copyfile(SOURCE / "pricing.json", copy / "pricing.json")
        state = root / "state"
        state.mkdir()
        settings = root / "settings"
        settings.mkdir()
        sentinels = root / "bin"
        sentinels.mkdir()
        hits_file = root / "sentinel-hits"
        for name in ("codex", "claude", "git", "curl", "ssh", "openclaw", "python", "python3"):
            path = sentinels / name
            path.write_text(f"#!/bin/sh\nprintf 'blocked\\n' >> '{hits_file}'\nexit 97\n")
            path.chmod(0o700)
        # No inherited keys, backend selection, market paths or controller paths.
        # HOME and CODEX_HOME are not repurposed; access to user settings is denied.
        os.environ.clear()
        os.environ.update(PATH=str(sentinels), TMPDIR=str(root), HARNESS="offline-sentinel",
                          MARKET_DIR=str(state), META_DIR=str(copy), META_PUSH="0",
                          XDG_CONFIG_HOME=str(settings), XDG_CACHE_HOME=str(root / "cache"),
                          AGENT_TOOLS=str(root / "tools"), CODEX_CWD_ROOT=str(root / "model-cwd"),
                          SHOP_PRICING=str(copy / "pricing.json"), SHOP_MEMORY=str(state / "memory.json"))
        tempfile.tempdir = str(root)
        runtime = tuple(Path(p).resolve() for p in (sys.prefix, sys.base_prefix))
        blocked = []
        active = [True]

        def deny(event, args):
            # Metadata only: never retain audited arguments, paths, values or locals.
            import re
            clean = lambda s: re.sub(r'[^A-Za-z0-9_.<>-]', '_', s)[:160]
            access, category = 'other', 'other'
            if event == 'open':
                path, mode, flags = args
                access = ('fd' if isinstance(path, int) else 'write' if
                          flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND) else 'read')
                if isinstance(path, (str, bytes, os.PathLike)):
                    target = Path(os.fsdecode(path)).resolve()
                    if target.is_relative_to(root):
                        category = 'temp'
                    elif any(target.is_relative_to(p) for p in runtime):
                        category = 'python-runtime'
                    elif str(target).startswith(('/System/', '/Library/', '/usr/', '/etc/', '/private/etc/')):
                        category = 'system'
                    elif str(target).startswith(('/Users/', '/home/')):
                        category = 'user'
            stack = []
            frame = sys._getframe(1)
            while frame and len(stack) < 20:
                stack.append(dict(module=clean(str(frame.f_globals.get('__name__', 'unknown'))),
                                  function=clean(frame.f_code.co_name),
                                  file=clean(Path(frame.f_code.co_filename).name), line=frame.f_lineno))
                frame = frame.f_back
            blocked.append(dict(event=event, access=access, category=category, stack=stack))
            raise PermissionError("offline runner blocked a forbidden boundary")

        def inside(path):
            return isinstance(path, (str, bytes, os.PathLike)) and Path(os.fsdecode(path)).resolve().is_relative_to(root)

        def audit(event, args):
            if not active[0]:
                return
            if event.startswith(("subprocess.", "socket.")) or event in (
                    "os.system", "os.posix_spawn", "os.fork", "os.forkpty", "os.exec", "os.spawn", "pty.spawn"):
                deny(event, args)
            if event == "sqlite3.connect":
                database = args[0]
                if database != ":memory:":
                    # Reject URI filenames: their native interpretation differs from Path.resolve().
                    if (not isinstance(database, (str, bytes, os.PathLike))
                            or os.fsdecode(database).startswith("file:") or not inside(database)):
                        deny(event, args)
            if event == "sqlite3.load_extension":
                deny(event, args)
            if event == "open":
                path, mode, flags = args
                if isinstance(path, int):
                    deny(event, args)  # No inherited file descriptors supplied to project code.
                writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
                if inside(path):
                    return
                resolved = Path(os.fsdecode(path)).resolve()
                if not writing and (any(resolved.is_relative_to(p) for p in runtime)
                                    or resolved == Path("/dev/null")):
                    return
                deny(event, args)
            mutation = {"os.remove": (0,), "os.rmdir": (0,), "os.mkdir": (0,), "os.chmod": (0,),
                        "os.rename": (0, 1), "os.link": (0, 1), "os.symlink": (0, 1),
                        "os.truncate": (0,), "os.utime": (0,), "os.chdir": (0,)}
            if event in mutation and any(not inside(args[i]) for i in mutation[event]):
                deny(event, args)

        sys.addaudithook(audit)
        result = None
        try:
            os.chdir(copy)
            sys.path[0] = str(copy)
            import sol_source_load
            sol_source_load.install()  # trusted fixture equivalent of the seller's first import
            suite = unittest.defaultTestLoader.loadTestsFromNames(names)
            print(f"Python {sys.version.split()[0]}; disposable source/state/settings/controllers; subprocess/socket denied",
                  flush=True)
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        finally:
            active[0] = False  # Trusted teardown and hash-only verification, after project code returned.
            os.chdir(original_cwd)
            after = manifest(protected)
            safe = before == after and not blocked and not hits_file.exists()
            tests_rc = 0 if result and result.wasSuccessful() else 1
            print(f"BLOCKED_DIAGNOSTICS {blocked}", flush=True)
            print(f"PROTECTED_STATIC before={before} after={after}; blocked={len(blocked)}; "
                  f"sentinel_hits={int(hits_file.exists())}; safety={'PASS' if safe else 'FAIL'}; "
                  f"tests_rc={tests_rc}; live_moving_state=NOT_COMPARED", flush=True)
        return tests_rc if tests_rc else (0 if safe else 3)


if __name__ == "__main__":
    sys.exit(main())
