"""Harness versions, atomic edits, revert and the breakage guard (human code, not editable by the model).

Safety only: an edit is checked for format (editable paths, JSON that parses, Python that compiles, harness.validate_dir
on a staged copy) and then swapped in whole. Every version is kept as a full copy under
<harness_root>/../library/harnesses/<agent>/<version>/, with library/INDEX.md (one line per stored version) and
library/versions.json (current, last_good, history). The breakage guard looks only at whether a harness works (runs
of decisions that fell back to the do-nothing action on format errors or timeouts), never at how well it performs.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import tempfile

from kernel import common as c
from kernel import harness as hz

AGENTS = ("seller", "buyer", "operator")
EDITABLE_DIRS = ("sub_agents", "skills", "scripts")
EFFECTS = ("도움", "해로움", "효과 없음", "판단 불가")
BREAKAGE_BOUNDS = (3, 50)
BREAKAGE_DEFAULT = 5


def library_dir(harness_root):
    return os.path.join(os.path.dirname(os.path.realpath(harness_root)), "library")


def _version_dir(harness_root, agent, version):
    return os.path.join(
        library_dir(harness_root), "harnesses", agent, str(int(version))
    )


@contextlib.contextmanager
def _locked(harness_root):
    lib = library_dir(harness_root)
    os.makedirs(lib, exist_ok=True)
    with open(os.path.join(lib, ".lock"), "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def load_state(harness_root):
    st = c.load_json(os.path.join(library_dir(harness_root), "versions.json"), {})
    return st if isinstance(st, dict) else {}


def _save_state(harness_root, st):
    _write_atomic(
        os.path.join(library_dir(harness_root), "versions.json"),
        json.dumps(st, ensure_ascii=False, indent=1),
    )
    _write_index(harness_root, st)


def _write_index(harness_root, st):
    lines = [
        "# 하네스 라이브러리",
        "",
        "판마다 한 줄: 에이전트, 판, 적용 시각, 이유, 바뀐 파일, 교훈 여부",
        "",
    ]
    for agent in AGENTS:
        for h in (st.get(agent) or {}).get("history") or []:
            v = h.get("version")
            when = c.kst(h["ts"]) if isinstance(h.get("ts"), (int, float)) else "-"
            files = ", ".join(h.get("files") or []) or "-"
            exp = os.path.isfile(
                os.path.join(_version_dir(harness_root, agent, v), "EXPERIENCE.md")
            )
            lines.append(
                f"- {agent} 판 {v} | {when} | {str(h.get('reason') or '')[:160]} | 파일 {files} | "
                f"교훈 {'있음' if exp else '없음'}"
            )
    _write_atomic(
        os.path.join(library_dir(harness_root), "INDEX.md"), "\n".join(lines) + "\n"
    )


def _read_version(d):
    try:
        with open(os.path.join(d, "VERSION"), encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _store(harness_root, agent, version, src):
    dst = _version_dir(harness_root, agent, version)
    if not os.path.isdir(dst):
        shutil.copytree(src, dst)


def _agent_state(harness_root, st, agent, now):
    """State for agent; the first call records the version on disk as the baseline and as last_good."""
    a = st.get(agent)
    if isinstance(a, dict) and a.get("current") is not None:
        return a
    cur = os.path.join(harness_root, agent)
    v = _read_version(cur)
    a = {
        "current": v,
        "last_good": v,
        "history": [{"version": v, "ts": now, "reason": "첫 판", "files": []}],
    }
    if v is not None and not hz.validate_dir(cur):
        _store(harness_root, agent, v, cur)
    st[agent] = a
    return a


def ensure_baseline(harness_root, agent, now=None):
    """Record and store the current on-disk version if this agent has no state yet."""
    now = c.now_ts() if now is None else now
    with _locked(harness_root):
        st = load_state(harness_root)
        had = agent in st
        a = _agent_state(harness_root, st, agent, now)
        if not had:
            _save_state(harness_root, st)
        return dict(a)


def path_error(rel):
    """Why rel is not an editable harness path, or None."""
    if not isinstance(rel, str) or not rel.strip():
        return f"path must be a non-empty string: {rel!r}"
    if os.path.isabs(rel) or rel.startswith(("/", "\\", "~")):
        return f"absolute path not allowed: {rel}"
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return f"path traversal not allowed: {rel}"
    if len(parts) == 1:
        if not rel.endswith((".md", ".json")):
            return f"top-level file must be .md or .json: {rel}"
    elif parts[0] not in EDITABLE_DIRS:
        return f"outside the editable set ({', '.join(EDITABLE_DIRS)}): {rel}"
    return None


def content_errors(rel, content):
    if content is None:
        return []
    if not isinstance(content, str):
        return [f"{rel}: content must be a string or null"]
    if rel.endswith(".json"):
        try:
            json.loads(content)
        except ValueError as e:
            return [f"{rel}: invalid JSON: {e}"]
    if rel.endswith(".py"):
        try:
            compile(content, rel, "exec")
        except (SyntaxError, ValueError) as e:
            return [f"{rel}: does not compile: {e}"]
    return []


def stage(src_dir, dst_dir, files, secrets=()):
    """Copy src_dir to dst_dir and apply files ({rel: content or None}). Returns format errors (empty = ok)."""
    if not isinstance(files, dict) or not files:
        return ["files must be a non-empty object {path: content or null}"]
    errors = []
    for rel, content in files.items():
        err = path_error(rel)
        errors += [err] if err else content_errors(rel, content)
    if errors:
        return errors
    shutil.copytree(src_dir, dst_dir, symlinks=True)  # a planted link stays a link and fails validate_dir
    for rel, content in files.items():
        p = os.path.join(dst_dir, *rel.replace("\\", "/").split("/"))
        try:
            if content is None:
                if not os.path.isfile(p):
                    errors.append(f"{rel}: cannot delete, no such file")
                else:
                    os.remove(p)
                continue
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(c.mask(content, secrets))
        except (OSError, ValueError) as e:  # NUL byte, name too long, parent is a file
            errors.append(f"{rel}: {type(e).__name__}")
    if errors:
        return errors
    return [f"validate: {e}" for e in hz.validate_dir(dst_dir)]


def _next_version(harness_root, agent, a):
    seen = [h.get("version") or 0 for h in a.get("history") or []] + [
        a.get("current") or 0
    ]
    lib = os.path.join(library_dir(harness_root), "harnesses", agent)
    if os.path.isdir(lib):
        seen += [int(n) for n in os.listdir(lib) if n.isdigit()]
    return max(seen) + 1


def _install(harness_root, agent, build, reason, names, now, extra=None):
    """build(staged_dir) -> errors. On success swap the staged dir in with a new VERSION and record it."""
    if agent not in AGENTS:
        return False, {"errors": [f"agent must be one of {AGENTS}"]}
    cur = os.path.join(harness_root, agent)
    with _locked(harness_root):
        st = load_state(harness_root)
        a = _agent_state(harness_root, st, agent, now)
        tmp = tempfile.mkdtemp(prefix=f".{agent}-stage-", dir=harness_root)
        try:
            new_dir = os.path.join(tmp, agent)
            errors = build(new_dir)
            if errors:
                return False, {"errors": errors}
            v = _next_version(harness_root, agent, a)
            with open(os.path.join(new_dir, "VERSION"), "w", encoding="utf-8") as f:
                f.write(f"{v}\n")
            errors = hz.validate_dir(new_dir)
            if errors:
                return False, {"errors": [f"validate: {e}" for e in errors]}
            old_v = _read_version(cur)
            if old_v is not None and not hz.validate_dir(cur):
                _store(harness_root, agent, old_v, cur)
            os.rename(cur, os.path.join(tmp, "old"))
            os.rename(new_dir, cur)
            _store(harness_root, agent, v, cur)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        a["current"] = v
        a.setdefault("history", []).append(
            dict(
                {
                    "version": v,
                    "ts": now,
                    "reason": c.mask(str(reason or ""))[:500],
                    "files": names,
                },
                **(extra or {}),
            )
        )
        _save_state(harness_root, st)
        return True, {"version": v, "previous": old_v}


def apply_edit(harness_root, agent, files, reason, secrets=(), now=None):
    """Apply {rel: content or None} to the agent's harness as a new version. -> (ok, {"version"} or {"errors"})."""
    now = c.now_ts() if now is None else now
    cur = os.path.join(harness_root, agent)
    names = sorted(files) if isinstance(files, dict) else []
    return _install(
        harness_root, agent, lambda d: stage(cur, d, files, secrets), reason, names, now
    )


def revert(harness_root, agent, version, reason=None, now=None):
    """Install the stored copy of `version` as a new version (versions only grow, so time windows stay clean)."""
    now = c.now_ts() if now is None else now
    try:
        src = _version_dir(harness_root, agent, version)
    except (TypeError, ValueError):
        return False, {"errors": [f"version must be an int: {version!r}"]}
    if not os.path.isdir(src):
        return False, {"errors": [f"{agent} version {version} is not in the library"]}

    def build(d):
        shutil.copytree(src, d, ignore=shutil.ignore_patterns("EXPERIENCE.md"))
        return []

    ok, info = _install(
        harness_root,
        agent,
        build,
        reason or f"판 {version}로 되돌림",
        [],
        now,
        {"restored_from": int(version)},
    )
    if ok:
        info["restored_from"] = int(version)
    return ok, info


def write_experience(harness_root, agent, version, effects, lesson):
    """library/harnesses/<agent>/<version>/EXPERIENCE.md with the effects table and the lesson; refresh INDEX.md."""
    d = _version_dir(harness_root, agent, version)
    lines = [f"# 경험: {agent} 판 {version}", "", "## 수정별 효과", ""]
    lines += [
        f"- {e.get('edit')}: {e.get('effect')} (근거: {e.get('evidence')})"
        for e in effects
    ]
    lines += ["", "## 교훈", "", str(lesson)]
    with _locked(harness_root):
        _write_atomic(os.path.join(d, "EXPERIENCE.md"), c.mask("\n".join(lines) + "\n"))
        _write_index(harness_root, load_state(harness_root))
    return os.path.join(d, "EXPERIENCE.md")


def bound_breakage_n(n):
    if not isinstance(n, int) or isinstance(n, bool):
        return BREAKAGE_DEFAULT
    return max(BREAKAGE_BOUNDS[0], min(BREAKAGE_BOUNDS[1], n))


def is_broken(row):
    """A decision that fell back to the do-nothing action on harness, format or timeout errors.

    Subscription refusals and other model-side failures (auth, crash) are not the harness's fault and do not count.
    """
    if row.get("route_used") != "safe":
        return False
    errs = [str(e) for e in row.get("errors") or []]
    if not errs:
        return False
    return not any(
        "ModelRefused" in e or ("ModelError" in e and "timeout" not in e) for e in errs
    )


def check_breakage(
    root,
    harness_root,
    traj,
    n,
    now=None,
    agents=("seller", "buyer"),
    max_bytes=20_000_000,
):
    """Mark last_good or auto-revert. -> {agent: revert info} for agents that were reverted.

    Per agent, only decisions made by the current version count. The last n all served (not the do-nothing
    fallback) -> current is last_good. The
    last n all broken and last_good differs -> install last_good again and write a harness_revert row.
    """
    now = c.now_ts() if now is None else now
    n = bound_breakage_n(n)
    rows = [
        r
        for r in c.tail_json(
            os.path.join(root, "a2/trajectory.jsonl"), max_bytes, b'"decision"'
        )
        if r.get("kind") == "decision"
    ]
    out = {}
    for agent in agents:
        a = ensure_baseline(harness_root, agent, now)
        cur = a.get("current")
        last = [
            r
            for r in rows
            if r.get("agent") == agent and r.get("harness_version") == cur
        ][-n:]
        if len(last) < n:
            continue
        if all(is_broken(r) for r in last):
            lg = a.get("last_good")
            if lg is None or lg == cur:
                continue
            reason = f"작동 불능: 판 {cur}의 최근 결정 {n}번이 모두 형식 오류 또는 시간 초과로 거절됨"
            ok, info = revert(harness_root, agent, lg, reason=reason, now=now)
            if ok:
                traj.append(
                    "operator",
                    "harness_revert",
                    target=agent,
                    version=info["version"],
                    restored_from=lg,
                    broken_version=cur,
                    reason=reason,
                )
                out[agent] = info
        elif all(r.get("route_used") != "safe" for r in last) and a.get("last_good") != cur:
            with _locked(harness_root):
                st = load_state(harness_root)
                st[agent]["last_good"] = cur
                _save_state(harness_root, st)
    return out


# ---------------------------------------------------------------- side-by-side trial (동시 비교, 2026-11-06)
# One harness version runs at a time, so a before/after comparison always mixes the version with the time of day and the
# request mix (live 11-06 01-09h: the operator judged 74 of 75 effects undecidable). A trial runs a candidate next to the
# current version at the same time: each new request goes to one arm by a hash of its task id, so both arms see the same
# hours and the same kind of requests. The kernel only assigns and counts; starting, adopting or dropping is the
# operator's call. One trial per agent; only the seller is wired (the buyer makes about 90 decisions an hour).
TRIAL_AGENTS = ("seller",)
# A trial blocks the agent's edits, so the kernel ends it without adopting after this many hours (성수님 결정 2026-11-06
# 16시: 판 5는 2 SE까지 17~43시간이 더 필요하다고 나왔고 그동안 판매자를 고칠 수 없었음). 요청이 판마다 시간당 약 150건이라
# 6시간이면 판마다 약 900건 (추정): 이 안에 드러나지 않는 차이는 하루를 막을 만큼 크지 않다고 봄
TRIAL_MAX_H = 6
TRIAL_SHARE = (0.1, 0.9, 0.5)  # min, max, default share of new requests sent to the candidate


def _trial_path(harness_root):
    return os.path.join(library_dir(harness_root), "trial.json")


def trial_state(harness_root):
    """{agent: {version, base, share, started_ts, reason}} of running trials."""
    st = c.load_json(_trial_path(harness_root), {})
    return {k: v for k, v in st.items() if isinstance(v, dict)} if isinstance(st, dict) else {}


def trial_dir(harness_root, agent, version):
    return _version_dir(harness_root, agent, version)


def trial_arm(harness_root, agent, key):
    """("trial" | "base", trial version or None) for one request. Deterministic in key, so a deal keeps its arm."""
    t = trial_state(harness_root).get(agent)
    if not t:
        return "base", None
    u = int(hashlib.sha256(f"{agent}:{t.get('version')}:{key}".encode()).hexdigest()[:8], 16) / 0x100000000
    return ("trial" if u < float(t.get("share") or 0) else "base"), t.get("version")


def bound_share(share):
    lo, hi, default = TRIAL_SHARE
    if not isinstance(share, (int, float)) or isinstance(share, bool):
        return default
    return max(lo, min(hi, float(share)))


def start_trial(harness_root, agent, files, share=None, reason=None, secrets=(), now=None):
    """Store the current version plus files as a candidate version in the library (not installed) and start the trial.
    -> (ok, {"version", "base", "share"} or {"errors"})."""
    now = c.now_ts() if now is None else now
    if agent not in TRIAL_AGENTS:
        return False, {"errors": [f"동시 비교는 {TRIAL_AGENTS}만 됨"]}
    cur = os.path.join(harness_root, agent)
    with _locked(harness_root):
        if agent in trial_state(harness_root):
            return False, {"errors": [f"{agent} 동시 비교가 이미 진행 중임. trial_end로 먼저 끝냄"]}
        st = load_state(harness_root)
        a = _agent_state(harness_root, st, agent, now)
        tmp = tempfile.mkdtemp(prefix=f".{agent}-trial-", dir=harness_root)
        try:
            new_dir = os.path.join(tmp, agent)
            errors = stage(cur, new_dir, files, secrets)
            if errors:
                return False, {"errors": errors}
            v = _next_version(harness_root, agent, a)
            with open(os.path.join(new_dir, "VERSION"), "w", encoding="utf-8") as f:
                f.write(f"{v}\n")
            errors = hz.validate_dir(new_dir)
            if errors:
                return False, {"errors": [f"validate: {e}" for e in errors]}
            _store(harness_root, agent, v, new_dir)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        base = _read_version(cur)
        t = {
            "version": v,
            "base": base,
            "share": bound_share(share),
            "started_ts": now,
            "reason": c.mask(str(reason or ""), secrets)[:500],
            "files": sorted(files),
        }
        trials = trial_state(harness_root)
        trials[agent] = t
        _write_atomic(_trial_path(harness_root), json.dumps(trials, ensure_ascii=False, indent=1))
        a.setdefault("history", []).append(
            {"version": v, "ts": now, "reason": f"동시 비교 후보: {t['reason']}"[:500], "files": t["files"], "trial": True}
        )
        _save_state(harness_root, st)
        return True, {"version": v, "base": base, "share": t["share"]}


def end_trial(harness_root, agent, adopt, reason=None, now=None):
    """Stop the trial; with adopt, install the candidate as the new current version.
    -> (ok, {"trial": {...}, "adopted": bool, "version"?: new current} or {"errors"})."""
    now = c.now_ts() if now is None else now
    with _locked(harness_root):
        trials = trial_state(harness_root)
        t = trials.pop(agent, None)
        if t is None:
            return False, {"errors": [f"{agent} 동시 비교가 진행 중이 아님"]}
        _write_atomic(_trial_path(harness_root), json.dumps(trials, ensure_ascii=False, indent=1))
    if not adopt:
        return True, {"trial": t, "adopted": False}
    ok, info = revert(harness_root, agent, t["version"], reason or f"동시 비교 후보 판 {t['version']} 채택", now)
    if not ok:
        return False, dict(info, trial=t)
    return True, {"trial": t, "adopted": True, "version": info["version"]}
