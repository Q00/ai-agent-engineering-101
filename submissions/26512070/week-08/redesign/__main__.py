"""py -m redesign ping [role|model]      # one OpenRouter call (default: every role in config.json)"""
import sys

from redesign import llm


def main(argv):
    if argv[:1] == ["ping"]:
        for m in argv[1:] or list(llm.config()["roles"]):
            print(m, llm.ask("say only: pong", model=m, tag="ping"))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
