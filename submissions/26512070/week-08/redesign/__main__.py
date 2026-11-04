"""py -m redesign ping [role|model]      # one OpenRouter call (default: every role in config.json)
py -m redesign rules                  # check rules.json, write work/rules.md
"""
import collections
import sys

from redesign import llm, rules


def main(argv):
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
