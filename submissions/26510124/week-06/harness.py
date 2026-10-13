"""First learning checkpoint: ask Claude Code for the seller's opening price.

Run directly with: python harness.py --item lamp --reserve 100
Follow-up negotiation and the buyer are later checkpoints.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import uuid


ROOT = Path(__file__).resolve().parent
COMMAND = [
    "claude", "-p", "--model", "haiku",
    "--tools", "", "--setting-sources", "", "--no-session-persistence",
    "--strict-mcp-config", "--disable-slash-commands",
]


class HarnessError(RuntimeError):
    """The harness did not return a usable decision; never invent a fallback."""


def parse_opening(output, reserve):
    """Validation checks the model's decision; it never changes its price."""
    try:
        decision = json.loads(output)
    except json.JSONDecodeError as exc:
        raise HarnessError("The harness response was not JSON") from exc
    if not isinstance(decision, dict):
        raise HarnessError("The harness response must be a JSON object")
    price = decision.get("price")
    # bool is a subclass of int in Python, but True is not an asking price.
    if type(price) is not int or price <= 0 or price < reserve:
        raise HarnessError("The harness price must be a positive integer >= reserve")
    if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
        raise HarnessError("The harness must explain its opening price")
    return decision


def opening_price(item, reserve, buyer_message):
    prompt = (ROOT / "prompts" / "seller_opening.txt").read_text(encoding="utf-8")
    prompt += "\n" + json.dumps({
        "item": item, "reserve": reserve, "buyer_message": buyer_message,
    }, ensure_ascii=False)
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    log_path = logs / f"opening-{uuid.uuid4().hex}.log"

    # A fresh file per attempt preserves errors and raw model output unchanged.
    with log_path.open("x", encoding="utf-8") as log:
        log.write(f"COMMAND: {shlex.join(COMMAND)}\nPROMPT:\n{prompt}\n")
        log.flush()
        try:
            # Do not load this course repository's instructions into the model.
            with tempfile.TemporaryDirectory(prefix="week06-harness-") as workdir:
                result = subprocess.run(
                    COMMAND, input=prompt, text=True, capture_output=True,
                    cwd=workdir, timeout=90,
                )
        except subprocess.TimeoutExpired as exc:
            for label, content in [("STDOUT", exc.stdout), ("STDERR", exc.stderr)]:
                if isinstance(content, bytes):
                    content = content.decode("utf-8", errors="replace")
                log.write(f"{label}:\n{content or ''}\n")
            log.write("ERROR: harness timed out after 90 seconds\n")
            raise HarnessError(f"Harness timed out; see {log_path.name}") from exc
        except OSError as exc:
            log.write(f"ERROR: {exc}\n")
            raise HarnessError(f"Cannot start Claude Code; see {log_path.name}") from exc

        log.write(f"EXIT: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\n")
        if result.returncode:
            raise HarnessError(f"Claude Code failed; see {log_path.name}")
        try:
            decision = parse_opening(result.stdout, reserve)
        except HarnessError as exc:
            log.write(f"VALIDATION ERROR: {exc}\n")
            raise HarnessError(f"{exc}; see {log_path.name}") from exc
        log.write("VALIDATION: PASS\n")
    return decision["price"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item", default="lamp")
    parser.add_argument("--reserve", type=int, default=100)
    args = parser.parse_args()
    price = opening_price(args.item, args.reserve, f"I want to buy a {args.item}")
    print(json.dumps({"item": args.item, "price": price}))
