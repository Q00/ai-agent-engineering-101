"""py -m redesign ping [role|model]      # one OpenRouter call (default: every role in config.json)
py -m redesign rules                  # check rules.json, write work/rules.md
"""
import collections
import sys

from redesign import llm, rules


def main(argv):
    if argv[:1] == ["extract"]:
        from redesign import extract
        src = argv[argv.index("--source") + 1] if "--source" in argv else None
        lim = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
        chunks, cands, errors = extract.run(src, lim)
        bad = sum(not c["quote_ok"] for c in cands)
        print(f"{len(chunks)} chunks, {len(cands)} candidates, {bad} with a quote not in the chunk, "
              f"{len(errors)} chunks failed")
        for e in errors:
            print("  failed", e["chunk"], e["error"][:120])
        return 0
    if argv[:1] == ["merge"]:
        from redesign import merge
        area = argv[argv.index("--area") + 1] if "--area" in argv else None
        for r in merge.run(area):
            if r.get("error"):
                print(r["area"], "failed:", r["error"][:150])
                continue
            conflicts = sum(bool(x.get("conflict")) for x in r["rules"])
            print(f"{r['area']}: {r['candidates']} candidates -> {len(r['rules'])} rules, {conflicts} conflicts, "
                  f"{len(r.get('dropped', []))} dropped, {len(r['missing'])} left out")
        return 0
    if argv[:1] == ["verify"]:
        from redesign import verify
        area = argv[argv.index("--area") + 1] if "--area" in argv else None
        for r in verify.run(area):
            if r.get("error"):
                print(r["area"], "failed:", r["error"][:150])
                continue
            c = collections.Counter(v["verdict"] for v in r["verdicts"])
            print(f"{r['area']}: {dict(c)}" + (f", not reviewed {r['missing']}" if r["missing"] else ""))
        return 0
    if argv[:1] == ["proposals"]:
        from redesign import review
        print(review.markdown())
        return 0
    if argv[:1] == ["approve"]:
        from redesign import review
        for pid, action in review.approve(argv[1:]):
            print(pid, action)
        return 0
    if argv[:1] == ["reject"]:
        from redesign import review
        review.reject(argv[1], " ".join(argv[2:]))
        return 0
    if argv[:1] == ["rules"]:
        path, rs = rules.render()
        print(path, len(rs), dict(collections.Counter(r["status"] for r in rs)))
        return 0
    if argv[:1] == ["ping"]:
        for m in argv[1:] or list(llm.config()["roles"]):
            print(m, llm.ask("say only: pong", model=m, tag="ping"))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
