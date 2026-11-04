"""py -m redesign ping      # one OpenRouter call through the redesign harness's model"""
import sys

from redesign import llm


def main(argv):
    if argv[:1] == ["ping"]:
        print(llm.ask("say only: pong", tag="ping"))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
