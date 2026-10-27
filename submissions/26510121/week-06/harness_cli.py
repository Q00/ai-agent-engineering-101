"""Non-interactive entry point; reads context on stdin, prints a validated decision."""
from negotiation.harness import main


if __name__ == "__main__":
    raise SystemExit(main())
