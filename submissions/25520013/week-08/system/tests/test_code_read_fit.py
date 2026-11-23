"""11-11 21:10: code_read answers fit what the operator is shown, and func= reads one whole function."""
import os

from kernel import code_edit, operator as op, tools


def _root(tmp_path, n=400):
    body = ["def first(a):", "    return a", "", "", "@staticmethod", "def target(x):"]
    body += [f"    y{i} = x + {i}  # {'pad ' * 12}" for i in range(n)]
    body += ["    return x", "", "", "def last():", "    return 0"]
    (tmp_path / "policy.py").write_text("\n".join(body) + "\n")
    os.makedirs(tmp_path / "hooks", exist_ok=True)
    return str(tmp_path)


def test_default_read_is_never_cut(tmp_path):
    out = tools.code_read(_root(tmp_path), "policy.py")
    assert len(out) <= op.obs_cap("code_read")
    assert "cut" not in op.obs_facts(out, op.obs_cap("code_read"))
    last = out.splitlines()[-1]
    assert last.startswith("[여기까지 온전히 보임. 이어 읽기: start=")
    shown_to = int(out.splitlines()[-2].split("|")[0])
    assert last == f"[여기까지 온전히 보임. 이어 읽기: start={shown_to + 1}]"


def test_func_reads_the_function_and_continues(tmp_path):
    root = _root(tmp_path)
    out = tools.code_read(root, "policy.py", func="target")
    head = out.splitlines()[0]
    assert "함수 target 5~" in head and "    5| @staticmethod" in out
    seen, start = [], None
    for _ in range(20):
        rows = [ln for ln in out.splitlines() if "| " in ln[:8]]
        seen += [int(r.split("|")[0]) for r in rows]
        tail = out.splitlines()[-1]
        if tail == "[함수 끝]":
            break
        start = int(tail.split("start=")[1].split(",")[0])
        out = tools.code_read(root, "policy.py", func="target", start=start)
        assert len(out) <= op.obs_cap("code_read")
    assert seen == list(range(5, 408)) and tail == "[함수 끝]"
    assert "def last" not in out


def test_func_missing_and_small(tmp_path):
    root = _root(tmp_path)
    assert tools.code_read(root, "policy.py", func="nope").startswith("인자 오류")
    out = tools.code_read(root, "policy.py", func="first(")
    assert out.splitlines()[1:] == ["    1| def first(a):", "    2|     return a", "[함수 끝]"]
    assert code_edit.read(root, "policy.py", 1, 2).splitlines()[-1] == "[여기까지 온전히 보임. 이어 읽기: start=3]"
