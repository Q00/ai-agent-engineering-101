"""Prepare a separate uploadable branch; never change original refs or live files.

Original commits and changed tree objects are recorded byte-for-byte so that
the verified original bundle plus the published branch can restore exact IDs.
Run without --apply first to inspect the bounded, single-log transformation.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess


def git(*args, data=None, env=None):
    return subprocess.run(["git", *args], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, env=env).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    base = plan["base"]
    tip = git("rev-parse", "HEAD").decode().strip()
    source_ref = "refs/heads/" + plan["source_branch"]
    target_ref = "refs/heads/" + plan["target_branch"]
    assert git("rev-parse", source_ref).decode().strip() == tip
    assert plan["source_branch"] != plan["target_branch"]
    path = "submissions/26510358/week-07/logs/market-kit.log"
    large = plan["oversized_blobs"]
    assert len(large) == 1 and large[0]["path"] == path
    oversized = large[0]["oid"]
    replacement = git("rev-parse", base + ":" + path).decode().strip()
    commits = git("rev-list", "--reverse", "--topo-order", base + ".." + tip).decode().splitlines()
    assert commits and int(git("cat-file", "-s", oversized)) > 100_000_000
    assert int(git("cat-file", "-s", replacement)) < 100_000_000
    if not args.apply:
        print(json.dumps({"source_tip": tip, "commits": len(commits), "target_ref": target_ref,
                          "oversized_log": oversized, "replacement_original_log": replacement}))
        return
    existing = subprocess.run(["git", "show-ref", "--verify", "--quiet", target_ref])
    if existing.returncode != 1:
        raise RuntimeError("Target branch already exists or could not be checked")

    tree_cache, original_objects, mapping = {}, {}, {}
    def retain(oid, kind):
        original_objects[oid] = {"type": kind, "base64": base64.b64encode(git("cat-file", kind, oid)).decode()}

    def replace_tree(oid, parts):
        key = (oid, tuple(parts))
        if key in tree_cache:
            return tree_cache[key]
        entries = [record for record in git("ls-tree", "-z", oid).split(b"\0") if record]
        changed = False
        for i, entry in enumerate(entries):
            header, name = entry.split(b"\t", 1)
            mode, kind, child = header.split(b" ")
            if name != parts[0].encode():
                continue
            if len(parts) > 1:
                assert kind == b"tree"
                updated = replace_tree(child.decode(), parts[1:])
            else:
                updated = replacement if child.decode() == oversized else child.decode()
            if updated != child.decode():
                entries[i] = b" ".join([mode, kind, updated.encode()]) + b"\t" + name
                changed = True
        if changed:
            retain(oid, "tree")
            result = git("mktree", "-z", data=b"\0".join(entries) + b"\0").decode().strip()
        else:
            result = oid
        tree_cache[key] = result
        return result

    for oid in commits:
        raw = git("cat-file", "commit", oid)
        headers, message = raw.split(b"\n\n", 1)
        lines = headers.splitlines()
        tree = next(line[5:].decode() for line in lines if line.startswith(b"tree "))
        parents = [line[7:].decode() for line in lines if line.startswith(b"parent ")]
        new_tree = replace_tree(tree, path.split("/"))
        new_parents = [mapping.get(parent, parent) for parent in parents]
        if new_tree == tree and new_parents == parents:
            mapping[oid] = oid
            continue
        retain(oid, "commit")
        env = os.environ.copy()
        env.pop("MARKET_OWNER", None)
        for header, prefix in [(b"author ", "GIT_AUTHOR_"), (b"committer ", "GIT_COMMITTER_")]:
            value = next(line[len(header):] for line in lines if line.startswith(header))
            identity = re.fullmatch(rb"(.*) <([^>]*)> (\d+) ([+-]\d{4})", value)
            if identity is None:
                raise ValueError("Cannot preserve commit identity")
            env[prefix + "NAME"] = identity[1].decode()
            env[prefix + "EMAIL"] = identity[2].decode()
            env[prefix + "DATE"] = "@" + identity[3].decode() + " " + identity[4].decode()
        command = ["-c", "commit.gpgsign=false", "commit-tree", new_tree]
        for parent in new_parents:
            command += ["-p", parent]
        annotated = message.rstrip(b"\n") + b"\n\nOriginal-Commit: " + oid.encode() + b"\n"
        mapping[oid] = git(*command, data=annotated, env=env).decode().strip()

    published = mapping[tip]
    diff = git("diff", "--name-only", tip, published).decode().splitlines()
    assert diff == [path], diff
    assert len(git("rev-list", base + ".." + published).decode().splitlines()) == len(commits)
    assert git("rev-parse", source_ref).decode().strip() == tip
    target = args.plan.parent
    with (target / "original-objects.json").open("x") as stream:
        json.dump(original_objects, stream, indent=2)
    result = {"source_tip": tip, "base": base, "target_ref": target_ref,
              "target_tip": published, "original_ref_unchanged": True,
              "only_changed_file": path, "replacement_blob": replacement,
              "commits": [{"original": oid, "published": mapping[oid]} for oid in commits],
              "restore_objects": "original-objects.json",
              "original_bundle": "../week07-completion-20261025T105223Z/original-unpublished.bundle"}
    with (target / "history-map.json").open("x") as stream:
        json.dump(result, stream, indent=2)
    # Create, never force-update, the new branch after all invariants pass.
    git("update-ref", target_ref, published, "0" * 40)
    print(json.dumps({"source_tip": tip, "target_tip": published, "commits": len(commits),
                      "changed_file": path, "original_objects_preserved": len(original_objects)}))


if __name__ == "__main__":
    main()
