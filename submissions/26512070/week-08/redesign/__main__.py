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
