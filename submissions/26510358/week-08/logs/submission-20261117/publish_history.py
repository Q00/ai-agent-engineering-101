"""Build the GitHub upload branch week-08-submit from week-08 without touching week-08.

GitHub rejects blobs over 100 MB. Two such logs are in week-08's history:
  week-07/logs/market-kit.log (171 MB)          -> the version the week-07 PR already uploaded
  week-08/logs/v3-rejoin-.../runtime-console.log (1.5 GB) -> a short text note naming the original blob
Every commit is replayed with the same author, committer, dates and message (plus an
Original-Commit: trailer). Nothing else changes. The original commits stay on week-08 in the
server's hub repository. The replay is deterministic, so running it again after new commits on
week-08 extends week-08-submit by fast-forward.

Usage (repo root): python submissions/26510358/week-08/logs/submission-20261117/publish_history.py [--apply]
"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path

SOURCE, TARGET, UPSTREAM = "week-08", "week-08-submit", "upstream/main"
W07_LOG = "submissions/26510358/week-07/logs/market-kit.log"
W08_LOG = "submissions/26510358/week-08/logs/v3-rejoin-20261027T105244Z/runtime-console.log"
HERE = Path(__file__).resolve().parent


def git(*args, data=None, env=None):
    return subprocess.run(["git", *args], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, env=env).stdout


def rev(x):
    return git("rev-parse", x).decode().strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    tip = rev("refs/heads/" + SOURCE)
    base = git("merge-base", tip, UPSTREAM).decode().strip()
    big07 = "8bd696b7147d250b1f2629e6a2a0ce0103fa3364"
    big08 = "7bef40d15ed988b1d4fb5e6cd001df75f767ea1f"
    note = (f"Omitted from the GitHub upload: this file is {int(git('cat-file', '-s', big08))} bytes "
            f"(GitHub limit 100 MB).\nOriginal blob {big08}, kept on branch {SOURCE} in the server's "
            f"hub repository.\nSee submissions/26510358/week-08/logs/submission-20261117/history-map.json\n")
    replace = {
        W07_LOG: (big07, rev("origin/week-07-submit:" + W07_LOG)),
        W08_LOG: (big08, git("hash-object", "-w", "--stdin", data=note.encode()).decode().strip()),
    }
    for path, (old, new) in replace.items():
        assert int(git("cat-file", "-s", old)) > 100_000_000 and int(git("cat-file", "-s", new)) < 100_000_000

    commits = git("rev-list", "--reverse", "--topo-order", base + ".." + tip).decode().splitlines()
    print(json.dumps({"source_tip": tip, "base": base, "commits": len(commits)}))
    if not args.apply:
        return

    cache, mapping = {}, {}

    def fix_tree(oid, prefix=""):
        if oid in cache:
            return cache[oid]
        entries = [e for e in git("ls-tree", "-z", oid).split(b"\0") if e]
        changed = False
        for i, entry in enumerate(entries):
            header, name = entry.split(b"\t", 1)
            mode, kind, child = header.split(b" ")
            path = prefix + name.decode()
            new = child.decode()
            if kind == b"tree" and any(p.startswith(path + "/") for p in replace):
                new = fix_tree(child.decode(), path + "/")
            elif path in replace and child.decode() == replace[path][0]:
                new = replace[path][1]
            if new != child.decode():
                entries[i] = b" ".join([mode, kind, new.encode()]) + b"\t" + name
                changed = True
        out = git("mktree", "-z", data=b"\0".join(entries) + b"\0").decode().strip() if changed else oid
        cache[oid] = out
        return out

    for oid in commits:
        raw = git("cat-file", "commit", oid)
        headers, message = raw.split(b"\n\n", 1)
        lines = headers.splitlines()
        if any(l.startswith(b"gpgsig") for l in lines):
            raise RuntimeError("signed commit " + oid)
        tree = next(l[5:].decode() for l in lines if l.startswith(b"tree "))
        parents = [l[7:].decode() for l in lines if l.startswith(b"parent ")]
        new_tree = fix_tree(tree)
        new_parents = [mapping.get(p, p) for p in parents]
        if new_tree == tree and new_parents == parents:
            mapping[oid] = oid
            continue
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
        for head, pre in [(b"author ", "GIT_AUTHOR_"), (b"committer ", "GIT_COMMITTER_")]:
            value = next(l[len(head):] for l in lines if l.startswith(head))
            m = re.fullmatch(rb"(.*) <([^>]*)> (\d+) ([+-]\d{4})", value)
            env[pre + "NAME"], env[pre + "EMAIL"] = m[1].decode(), m[2].decode()
            env[pre + "DATE"] = "@" + m[3].decode() + " " + m[4].decode()
        cmd = ["-c", "commit.gpgsign=false", "commit-tree", new_tree]
        for p in new_parents:
            cmd += ["-p", p]
        msg = message.rstrip(b"\n") + b"\n\nOriginal-Commit: " + oid.encode() + b"\n"
        mapping[oid] = git(*cmd, data=msg, env=env).decode().strip()

    published = mapping[tip]
    diff = sorted(git("diff", "--name-only", tip, published).decode().splitlines())
    assert diff == sorted(replace), diff
    assert rev("refs/heads/" + SOURCE) == tip
    old = subprocess.run(["git", "rev-parse", "--verify", "-q", "refs/heads/" + TARGET],
                         stdout=subprocess.PIPE).stdout.decode().strip()
    if old:
        assert subprocess.run(["git", "merge-base", "--is-ancestor", old, published]).returncode == 0, \
            "replay is not a fast-forward of the existing " + TARGET
    git("update-ref", "refs/heads/" + TARGET, published, old or "0" * 40)
    result = {"source_branch": SOURCE, "source_tip": tip, "target_branch": TARGET,
              "target_tip": published, "base": base,
              "replaced": {p: {"original_blob": o, "published_blob": n,
                               "original_bytes": int(git("cat-file", "-s", o))}
                           for p, (o, n) in replace.items()},
              "commits": [{"original": c, "published": mapping[c]} for c in commits]}
    (HERE / "history-map.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({"target_tip": published, "rewritten": sum(mapping[c] != c for c in commits)}))


if __name__ == "__main__":
    main()
