"""v1.18 decision-path isolation (report 2): an AST import walk, transitive within the repo, no regex. Module names
resolve against the r3 root and r3/hooks (the hooks run with hooks/ on sys.path). A decision-path module must never
reach memory, harness, experiments, evals, taste, memo_proposer or llm, directly or through another repo module.
Dynamic imports with a constant name (importlib.import_module("x"), __import__("x")) count as imports."""

import ast
from pathlib import Path

import pytest

R3 = Path(__file__).resolve().parents[1]
ROOTS = (R3, R3 / "hooks")
FORBIDDEN = {"memory", "harness", "experiments", "evals", "taste", "memo_proposer", "llm"}
# 2026-11-04 (성수님: the LLM is a required design element): the seller may reach exactly one LLM entry, the
# interpreter assist llm.live_v120c (rule parse first, LLM only when it fails; guarded import, OFF on any error).
# Nothing else in FORBIDDEN, and no other llm module, may be reached from the decision path.
ALLOWED = {("hooks/seller.py", "llm.live_v120c")}
DECISION = [f"hooks/{m}.py" for m in ("seller", "market_buyer", "restock", "review", "common")] + [
    f"{m}.py" for m in ("policy", "aggregates", "cards", "ledger", "reputation", "volume", "metrics", "owner")]


def imported_names(path):
    """[(level, dotted name, member)] for every import in the file, also inside functions. member=True for the
    `b` of `from a import b`: it may be a submodule (resolved and walked) or a plain name (not checked by name)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(0, a.name, False) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            out.append((n.level, base, False))
            out += [(n.level, f"{base}.{a.name}" if base else a.name, bool(base)) for a in n.names if a.name != "*"]
        elif (isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant)
              and isinstance(n.args[0].value, str)):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name in ("import_module", "__import__"):
                out.append((0, n.args[0].value, False))
    return [x for x in out if x[1]]


def resolve(name, roots):
    """Repo files a dotted name loads: each package __init__ on the way and the module itself."""
    files = []
    for root in roots:
        parts, d = name.split("."), Path(root)
        for i, p in enumerate(parts):
            last = i == len(parts) - 1
            if (d / p / "__init__.py").is_file():
                d = d / p
                files.append(d / "__init__.py")
            elif last and (d / f"{p}.py").is_file():
                files.append(d / f"{p}.py")
                break
            else:
                break
        if files:
            return files
    return files


def forbidden_path(f):
    rel = Path(f).resolve().relative_to(R3) if Path(f).resolve().is_relative_to(R3) else None
    return rel is not None and (rel.parts[0] in FORBIDDEN or (len(rel.parts) == 1 and rel.stem in FORBIDDEN))


def reach(start, roots=ROOTS):
    """-> list of 'chain' strings, one per forbidden module reached from `start` (empty = isolated)."""
    hits, seen, todo = [], set(), [(Path(start).resolve(), str(Path(start).name))]
    while todo:
        f, chain = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for level, name, member in imported_names(f):
            if level:
                base = f.parent
                for _ in range(level - 1):
                    base = base.parent
                targets = resolve(name, (base,))
            else:
                if name.split(".")[0] in FORBIDDEN:
                    if f.is_relative_to(R3) and (str(f.relative_to(R3)), name) in ALLOWED:
                        continue
                    if not member:  # `from memory import graph` is one hit, "memory"
                        hits.append(f"{chain} -> {name}")
                    continue
                targets = resolve(name, roots)
            for t in targets:
                t = t.resolve()
                if forbidden_path(t):
                    hits.append(f"{chain} -> {name} ({t.relative_to(R3)})")
                elif t not in seen:
                    todo.append((t, f"{chain} -> {name}"))
    return sorted(set(hits))


@pytest.mark.parametrize("mod", DECISION)
def test_decision_module_is_isolated(mod):
    assert (R3 / mod).is_file(), mod
    assert reach(R3 / mod) == []


def test_walk_is_transitive_through_hooks_common():
    """hooks/seller.py reaches policy.py via `from common import ...` (resolved against r3/hooks)."""
    names = set()
    for _, n, _ in imported_names(R3 / "hooks" / "common.py"):
        names.add(n)
    assert {"policy", "ledger", "metrics"} <= names
    assert resolve("common", ROOTS) == [R3 / "hooks" / "common.py"]


def test_negative_control_direct_and_transitive(tmp_path):
    (tmp_path / "direct.py").write_text("import json\nfrom memory.graph import Graph\n", encoding="utf-8")
    (tmp_path / "lazy.py").write_text("def f():\n    import importlib\n    return importlib.import_module('taste')\n",
                                      encoding="utf-8")
    (tmp_path / "middle.py").write_text("import direct\n", encoding="utf-8")
    (tmp_path / "top.py").write_text("import middle\nimport policy\n", encoding="utf-8")
    roots = (tmp_path,) + ROOTS
    assert reach(tmp_path / "direct.py", roots) == ["direct.py -> memory.graph"]
    assert reach(tmp_path / "lazy.py", roots) == ["lazy.py -> taste"]
    assert reach(tmp_path / "top.py", roots) == ["top.py -> middle -> direct -> memory.graph"]
    (tmp_path / "clean.py").write_text("import policy\nfrom ledger import Ledger\n", encoding="utf-8")
    assert reach(tmp_path / "clean.py", roots) == []


def test_negative_control_star_import_and_package_member(tmp_path):
    """`from x import *` and `from memory import graph` are flagged once, by the module name."""
    (tmp_path / "m.py").write_text("from memo_proposer import *\n", encoding="utf-8")
    assert reach(tmp_path / "m.py", (tmp_path,) + ROOTS) == ["m.py -> memo_proposer"]
    (tmp_path / "n.py").write_text("from memory import graph\n", encoding="utf-8")
    assert reach(tmp_path / "n.py", (tmp_path,) + ROOTS) == ["n.py -> memory"]


def test_seller_llm_assist_import_is_guarded():
    """The one allowed LLM edge (ALLOWED) sits inside try/except, so an import or config error leaves the rule path."""
    tree = ast.parse((R3 / "hooks/seller.py").read_text(encoding="utf-8"))
    guarded = [m for n in ast.walk(tree) if isinstance(n, ast.Try) for m in ast.walk(n)
               if isinstance(m, ast.ImportFrom) and m.module == "llm.live_v120c"]
    plain = [n for n in tree.body if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("llm")
             or isinstance(n, ast.Import) and any(a.name.split(".")[0] == "llm" for a in n.names)]
    assert guarded and not plain
