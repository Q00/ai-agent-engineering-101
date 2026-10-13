"""Call Claude Code for bargaining decisions, keeping every raw attempt.

Prompts choose strategy; validators only enforce the contract and price limits.
Run the opening probe with: python harness.py --item lamp --reserve 100
"""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parent
COMMAND = [
    "claude", "-p", "--model", "haiku",
    "--tools", "", "--setting-sources", "", "--no-session-persistence",
    "--strict-mcp-config", "--disable-slash-commands",
]


class HarnessError(RuntimeError):
    """The harness did not return a usable decision; never invent a fallback."""


def parse_object(output):
    # Some print-mode replies wrap otherwise valid JSON in one Markdown block.
    # Only unwrap a complete block; do not guess JSON out of surrounding prose.
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", output.strip(), re.DOTALL | re.IGNORECASE)
    if fenced:
        output = fenced.group(1)
    try:
        decision = json.loads(output)
    except json.JSONDecodeError as exc:
        raise HarnessError("The harness response was not JSON") from exc
    if not isinstance(decision, dict):
        raise HarnessError("The harness response must be a JSON object")
    if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
        raise HarnessError("The harness must explain its decision")
    return decision


def parse_opening(output, reserve):
    """Validation checks the model's decision; it never changes its price."""
    decision = parse_object(output)
    price = decision.get("price")
    # bool is a subclass of int in Python, but True is not an asking price.
    if type(price) is not int or price <= 0 or price < reserve:
        raise HarnessError("The harness price must be a positive integer >= reserve")
    return decision


def parse_seller(output, reserve, offer):
    decision = parse_object(output)
    if decision.get("action") not in ("counter", "accept", "reject"):
        raise HarnessError("Seller action must be counter, accept or reject")
    if decision["action"] != "reject":
        price = decision.get("price")
        if type(price) is not int or price <= 0 or price < reserve:
            raise HarnessError("Seller price must be a positive integer >= reserve")
        if decision["action"] == "accept" and price != offer:
            raise HarnessError("Accept must use the buyer's exact offered price")
    return decision


def parse_buyer(output, budget):
    decision = parse_object(output)
    if decision.get("action") not in ("offer", "accept", "walk_away"):
        raise HarnessError("Buyer action must be offer, accept or walk_away")
    if decision["action"] != "walk_away":
        price = decision.get("price")
        if type(price) is not int or price <= 0 or price > budget:
            raise HarnessError("Buyer price must be a positive integer <= budget")
    return decision


def decide(prompt_name, context, validate, *, timeout=90, prefix="decision"):
    prompt = (ROOT / "prompts" / prompt_name).read_text(encoding="utf-8")
    prompt += "\n" + json.dumps(context, ensure_ascii=False)
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    deadline = time.monotonic() + timeout
    # One correction request is allowed for invalid output, within one deadline.
    # Authentication/process errors fail immediately; no substitute policy runs.
    for attempt in range(2):
        log_path = logs / f"{prefix}-{uuid.uuid4().hex}.log"
        with log_path.open("x", encoding="utf-8") as log:
            log.write(f"COMMAND: {shlex.join(COMMAND)}\nPROMPT:\n{prompt}\n")
            log.flush()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.write("ERROR: no time remaining for the harness\n")
                raise HarnessError(f"Harness deadline expired; see {log_path.name}")
            try:
                # Run away from repository instructions, with tools/MCP disabled.
                with tempfile.TemporaryDirectory(prefix="week06-harness-") as workdir:
                    result = subprocess.run(
                        COMMAND, input=prompt, text=True, capture_output=True,
                        cwd=workdir, timeout=remaining,
                    )
            except subprocess.TimeoutExpired as exc:
                for label, content in [("STDOUT", exc.stdout), ("STDERR", exc.stderr)]:
                    if isinstance(content, bytes):
                        content = content.decode("utf-8", errors="replace")
                    log.write(f"{label}:\n{content or ''}\n")
                log.write(f"ERROR: harness timed out within {timeout} seconds\n")
                raise HarnessError(f"Harness timed out; see {log_path.name}") from exc
            except OSError as exc:
                log.write(f"ERROR: {exc}\n")
                raise HarnessError(f"Cannot start Claude Code; see {log_path.name}") from exc

            log.write(f"EXIT: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\n")
            if result.returncode:
                raise HarnessError(f"Claude Code failed; see {log_path.name}")
            try:
                decision = validate(result.stdout)
            except HarnessError as exc:
                log.write(f"VALIDATION ERROR: {exc}\n")
                if attempt == 1:
                    raise HarnessError(f"{exc}; see {log_path.name}") from exc
                prompt += "\nYour last output failed validation: " + str(exc)
                prompt += "\nReturn a corrected decision. Previous output as JSON string: " + json.dumps(result.stdout)
            else:
                log.write("VALIDATION: PASS\n")
                return decision


def opening_price(item, reserve, buyer_message):
    return decide("seller_opening.txt", {
        "item": item, "reserve": reserve, "buyer_message": buyer_message,
    }, lambda output: parse_opening(output, reserve), prefix="opening")["price"]


def seller_decision(*, item, reserve, offer, history):
    return decide("seller.txt", {
        "item": item, "reserve": reserve, "buyer_offer": offer, "history": history,
    }, lambda output: parse_seller(output, reserve, offer), prefix="seller")


def buyer_decision(*, item, budget, seller_message, history, timeout=90):
    return decide("buyer.txt", {
        "item": item, "budget": budget, "seller_message": seller_message, "history": history,
    }, lambda output: parse_buyer(output, budget), timeout=timeout, prefix="buyer")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item", default="lamp")
    parser.add_argument("--reserve", type=int, default=100)
    args = parser.parse_args()
    price = opening_price(args.item, args.reserve, f"I want to buy a {args.item}")
    print(json.dumps({"item": args.item, "price": price}))
