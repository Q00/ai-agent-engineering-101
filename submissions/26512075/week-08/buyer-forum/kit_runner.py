"""Run market kit v4 with its forum sender replaced by the approved queue."""

import argparse
import importlib.util
import sys
from pathlib import Path

if __package__:
    from .publisher import dispatch
else:
    from publisher import dispatch


def attach_approved_sender(kit):
    if getattr(kit, "KIT_VERSION", None) != "4" or not callable(getattr(kit, "outbox", None)):
        raise ValueError("This bridge supports the checked market kit v4 only")
    warned = False
    previous = None

    def approved_only(market, state):
        nonlocal warned, previous
        if not warned and (state / "outbox.jsonl").exists() and (state / "outbox.jsonl").stat().st_size:
            kit.log("[forum guard] standard outbox remains pending; approval is required")
            warned = True
        try:
            result = dispatch(state, market)
            if result != previous and result not in {"empty", "waiting-for-buyer"}:
                kit.log(f"[forum guard] {result}")
            previous = result
        except Exception as exc:
            kit.log(f"[forum guard] stopped: {type(exc).__name__}")

    kit.outbox = approved_only


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", required=True, type=Path, help="path to official market_kit.py")
    parser.add_argument("kit_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    forwarded = args.kit_args
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    if not forwarded or forwarded[0] != "join":
        parser.error("pass the normal kit join arguments after --")
    kit_path = args.kit.resolve()
    if not kit_path.is_file():
        parser.error("market kit file not found")
    sys.path.insert(0, str(kit_path.parent))
    spec = importlib.util.spec_from_file_location("forum_market_kit", kit_path)
    if spec is None or spec.loader is None:
        parser.error("cannot load market kit")
    kit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = kit
    spec.loader.exec_module(kit)
    attach_approved_sender(kit)
    sys.argv = [str(kit_path), *forwarded]
    kit.main()


if __name__ == "__main__":
    main()
