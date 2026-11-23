"""Week 04: two-agent price negotiation in free, tagged, and structured formats.

Run in small batches (recommended for a free API key):
    python negotiation.py --max-episodes 1
    python negotiation.py --max-episodes 1
Or run all remaining episodes:
    python negotiation.py

Completed (condition, repeat, scenario) tuples are skipped on restart.
Failed episodes remain as rows with blank measurements and may be retried later.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import traceback
from pathlib import Path

from openai import OpenAI, RateLimitError


ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "scenarios.json"
RESULTS = ROOT / "results.csv"
LOGS = ROOT / "logs"
HEADER = [
    "run", "condition", "scenario", "deal_possible", "outcome", "price",
    "correct", "violation", "turns", "format_errors", "reader_calls", "note",
]
CONDITIONS = ("free", "tagged", "structured")
ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")
BASE_URL = os.getenv("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
MODEL = os.getenv("AGENT_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
TEMPERATURE = float(os.getenv("AGENT_TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "350"))
TURN_LIMIT = int(os.getenv("AGENT_TURN_LIMIT", "6"))
REQUEST_INTERVAL = float(os.getenv("AGENT_REQUEST_INTERVAL", "4"))
MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", "3"))

FORMAT = {
    "free": (
        "Reply in plain English. Express your chosen action naturally; no tags or JSON. "
        "When making an offer state a clear integer price. When accepting, explicitly "
        "agree to the last price proposed by the other side."
    ),
    "tagged": (
        "Start every message with exactly one tag: (propose), (accept-proposal), "
        "(reject-proposal), or (refuse). Then write plain English. For (propose) "
        "state one integer price. For (accept-proposal), accept ONLY the other "
        "side's last proposed price."
    ),
    "structured": (
        "Reply with exactly one JSON object, with performative one of propose, "
        "accept-proposal, reject-proposal, refuse; and content an object. "
        'For propose include an integer price: {"performative":"propose",'
        '"content":{"price":70}}. For other acts use "content":{}. '
        "No code fences, commentary, or extra fields."
    ),
}

READER_SYSTEM = (
    "You are a protocol reader, not a negotiating party. Interpret ONE message "
    "and return exactly one JSON object with keys performative and price. "
    "performative MUST be one of propose, accept-proposal, reject-proposal, refuse. "
    "For a price proposal, price MUST be an integer; for all other acts, price "
    "MUST be null. If the message is a question without a matching act, use refuse. "
    "Do not change the act based on whether an offer is economically attractive. "
    "Do not add commentary or markdown."
)

TAG_PATTERN = re.compile(
    r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)(?:\s|$)",
    re.IGNORECASE,
)


class PauseForQuota(Exception):
    """A rate limit stopped a whole episode; resume later."""


class MessageError(Exception):
    """A model or reader message did not conform to the protocol."""


class ModelResponseError(Exception):
    """Provider returned a response object without usable message text."""


def client() -> OpenAI:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set in this terminal")
    return OpenAI(api_key=key, base_url=BASE_URL)


def model_text(api: OpenAI, messages: list[dict[str, str]]) -> str:
    # All agents and the reader use the same model, temperature and max_tokens.
    for attempt in range(MAX_RETRIES + 1):
        if REQUEST_INTERVAL > 0:
            time.sleep(REQUEST_INTERVAL)
        try:
            request = dict(
                model=MODEL,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                messages=messages,
            )
            # OpenRouter reasoning models may expose hidden reasoning in text;
            # disable it there. Other OpenAI-compatible providers (for example
            # Gemini or local Ollama) may reject this OpenRouter-specific body.
            if "openrouter.ai" in BASE_URL.lower():
                request["extra_body"] = {"reasoning": {"enabled": False}}
            response = api.chat.completions.create(**request)
            choices = getattr(response, "choices", None)
            if not choices:
                raise ModelResponseError("provider response contained no choices")
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if not isinstance(content, str) or not content.strip():
                raise ModelResponseError("provider response contained no usable text")
            return content
        except RateLimitError as exc:
            if attempt == MAX_RETRIES:
                # Never copy the provider's exception text to a public log.
                raise PauseForQuota("HTTP 429 rate limit; resume after quota recovery") from exc
            wait = max(20.0, REQUEST_INTERVAL * (2 ** (attempt + 2)))
            print(f"[RATE LIMIT] Waiting {wait:g}s before retry {attempt + 1}/{MAX_RETRIES}")
            time.sleep(wait)
        except (ModelResponseError, TypeError, IndexError, AttributeError) as exc:
            if attempt == MAX_RETRIES:
                raise ModelResponseError(type(exc).__name__) from exc
            wait = max(5.0, REQUEST_INTERVAL * (attempt + 1))
            print(f"[MODEL RESPONSE] malformed provider reply; waiting {wait:g}s before retry "
                  f"{attempt + 1}/{MAX_RETRIES}")
            time.sleep(wait)
    raise AssertionError("unreachable")


def json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip()
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise MessageError("JSON response must be an object")
    return value


def checked_price(value: object) -> int:
    if type(value) is not int or value < 0:
        raise MessageError("proposed price must be a nonnegative integer")
    return value


def validate(performative: object, price: object) -> tuple[str, int | None]:
    if performative not in ACTS:
        raise MessageError("unknown performative")
    if performative == "propose":
        return str(performative), checked_price(price)
    if price is not None:
        raise MessageError("only propose may contain a price")
    return str(performative), None


def read_message(api: OpenAI, condition: str, raw: str, log) -> tuple[str, int | None, int]:
    """Return (act, price, reader_model_calls). Parse failures retain call cost."""
    if condition == "structured":
        try:
            obj = json_object(raw)
            content = obj.get("content")
            if not isinstance(content, dict):
                raise MessageError("content must be a JSON object")
            act, price = validate(obj.get("performative"), content.get("price"))
            log(f"[PARSE structured] performative={act} price={price}")
            return act, price, 0
        except (ValueError, MessageError, TypeError) as exc:
            log(f"[PARSE structured] error={type(exc).__name__}")
            raise MessageError("structured message parse failed") from exc

    if condition == "tagged":
        match = TAG_PATTERN.match(raw)
        if not match:
            log("[PARSE tagged] error=invalid or missing performative tag")
            raise MessageError("tag missing")
        act = match.group(1).lower()
        if act != "propose":
            log(f"[PARSE tagged] performative={act} price=None")
            return act, None, 0
        # The tag is read by regex; the reader is used ONLY to obtain price.
        try:
            answer = model_text(api, [
                {"role": "system", "content": READER_SYSTEM},
                {"role": "user", "content": "Read the integer proposal price in this message. "
                 "Return the standard JSON object with performative=propose.\nMessage: " + raw},
            ])
        except PauseForQuota:
            raise
        except Exception as exc:
            log(f"[READER tagged] error={type(exc).__name__} calls=1")
            raise MessageError("tagged reader API error") from exc
        try:
            obj = json_object(answer)
            _, price = validate("propose", obj.get("price"))
            log(f"[READER tagged] tag=propose price={price} calls=1")
            return "propose", price, 1
        except (ValueError, MessageError, TypeError) as exc:
            log(f"[READER tagged] error={type(exc).__name__} calls=1 response={answer[:100]!r}")
            raise MessageError("tagged price reader failed") from exc

    # Free condition: an independent model must label EVERY message.
    try:
        answer = model_text(api, [
            {"role": "system", "content": READER_SYSTEM},
            {"role": "user", "content": "Label this negotiation message:\n" + raw},
        ])
    except PauseForQuota:
        raise
    except Exception as exc:
        log(f"[READER free] error={type(exc).__name__} calls=1")
        raise MessageError("free reader API error") from exc
    try:
        obj = json_object(answer)
        act = obj.get("performative")
        if act not in ACTS:
            raise MessageError("unknown performative")
        # The free-form reader sometimes repeats the accepted/rejected price
        # even though our reader schema requests null. That still gives an
        # unambiguous speech act, so ignore price for non-proposals instead of
        # counting the original agent message as unparseable.
        price = checked_price(obj.get("price")) if act == "propose" else None
        log(f"[READER free] performative={act} price={price} calls=1")
        return str(act), price, 1
    except (ValueError, MessageError, TypeError) as exc:
        log(f"[READER free] error={type(exc).__name__} calls=1 response={answer[:100]!r}")
        raise MessageError("free reader parse failed") from exc


def role_prompt(role: str, scenario: dict, condition: str) -> str:
    if role == "buyer":
        private = f"Your private maximum budget is {scenario['budget']} units. Never accept or propose a price above it."
        opening = "You must open the negotiation with a message."
    else:
        private = f"Your private minimum reserve price is {scenario['reserve']} units. Never accept or propose a price below it."
        opening = "Respond to the buyer's messages, not to a separate hidden instruction."
    common = (
        f"You are the {role} negotiating the price of a {scenario['item']} with one other agent. "
        f"{private} Do not reveal your private limit. {opening} "
        "Use only these four communication acts: propose an integer price, accept-proposal "
        "(agree to the other side's LAST proposed price), reject-proposal (decline but "
        "continue negotiating), or refuse (walk away and end with no deal). "
        "Never accept your own offer. Do not accept when the other side has not "
        "made an outstanding offer. Keep each message short. "
        "Do not invent the other side's private limit."
    )
    return common + "\nMESSAGE FORMAT: " + FORMAT[condition]


def agent_reply(api: OpenAI, role: str, scenario: dict, condition: str,
                history: list[dict], outstanding: dict | None) -> str:
    visible = [
        {"speaker": entry["speaker"], "message": entry["raw"]}
        for entry in history
    ]
    other_offer = (
        {"price": outstanding["price"], "speaker": outstanding["speaker"]}
        if outstanding is not None and outstanding["speaker"] != role else None
    )
    info = (
        "Conversation so far:\n" + json.dumps(visible, ensure_ascii=False)
        + "\nOther side's outstanding offer (if any): "
        + json.dumps(other_offer)
        + "\nSend only your next message in the required format."
    )
    return model_text(api, [
        {"role": "system", "content": role_prompt(role, scenario, condition)},
        {"role": "user", "content": info},
    ])


def evaluate(scenario: dict, outcome: str, price: int | None) -> tuple[int, int]:
    possible = scenario["reserve"] <= scenario["budget"]
    violation = int(
        outcome == "deal"
        and (price is None or price < scenario["reserve"] or price > scenario["budget"])
    )
    correct = int(
        (outcome == "deal" and possible and violation == 0)
        or (outcome == "no_deal" and not possible)
    )
    return correct, violation


def episode(api: OpenAI, scenario: dict, condition: str, log) -> dict:
    history: list[dict] = []
    outstanding: dict | None = None
    errors = 0
    reader_calls = 0
    outcome = "open"
    deal_price: int | None = None

    for step in range(TURN_LIMIT):
        role = "buyer" if step % 2 == 0 else "seller"
        raw = agent_reply(api, role, scenario, condition, history, outstanding)
        log(f"[MESSAGE {step + 1}] {role}: {raw!r}")
        try:
            act, price, calls = read_message(api, condition, raw, log)
            reader_calls += calls
        except PauseForQuota:
            raise
        except MessageError:
            errors += 1
            # A failed reader still incurred one reader model call in free,
            # or tagged if there was a syntactically valid propose tag.
            if condition == "free" or (condition == "tagged" and TAG_PATTERN.match(raw)
                                       and TAG_PATTERN.match(raw).group(1).lower() == "propose"):
                reader_calls += 1
            history.append({"speaker": role, "raw": raw, "act": "unparseable"})
            log("[PROTOCOL] unparseable; continue to next turn")
            continue

        history.append({"speaker": role, "raw": raw, "act": act, "price": price})
        if act == "refuse":
            outcome = "no_deal"
            log("[END] refuse; no deal")
            break
        if act == "propose":
            outstanding = {"speaker": role, "price": price}
            continue
        if act == "accept-proposal":
            if outstanding is None or outstanding["speaker"] == role:
                errors += 1
                log("[PROTOCOL] invalid acceptance without an outstanding other-side proposal")
                continue
            deal_price = outstanding["price"]
            outcome = "deal"
            log(f"[END] acceptance of {outstanding['speaker']}'s offer at {deal_price}")
            break
        # Reject-proposal can continue; it removes the previously offered price.
        if act == "reject-proposal":
            outstanding = None
    else:
        log(f"[END] turn limit {TURN_LIMIT}; open")

    correct, violation = evaluate(scenario, outcome, deal_price)
    return dict(deal_possible=int(scenario["reserve"] <= scenario["budget"]),
                outcome=outcome, price=deal_price, correct=correct,
                violation=violation, turns=len(history),
                format_errors=errors, reader_calls=reader_calls,
                note="")


def load_completed() -> set[tuple[str, str, str]]:
    completed = set()
    if not RESULTS.exists():
        return completed
    with RESULTS.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != HEADER:
            raise ValueError("results.csv has an unexpected header")
        for row in reader:
            # Blank result fields denote crashed episodes and are not skipped
            # on restart. Their original records are retained.
            if row["outcome"] in ("deal", "no_deal", "open"):
                completed.add((row["run"], row["condition"], row["scenario"]))
    return completed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--condition", choices=("all",) + CONDITIONS, default="all")
    parser.add_argument("--max-episodes", type=int, default=0,
                        help="max newly attempted episodes in this invocation, including failures; 0=all")
    args = parser.parse_args()
    if args.repeats < 1 or args.max_episodes < 0 or TURN_LIMIT < 1:
        raise SystemExit("Invalid repeats, max-episodes, or turn limit")
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if len(scenarios) < 4:
        raise SystemExit("Need at least four scenarios")
    if RESULTS.exists() and RESULTS.stat().st_size == 0:
        raise SystemExit("results.csv is empty; restore it or remove it before starting")
    LOGS.mkdir(exist_ok=True)
    if not RESULTS.exists():
        with RESULTS.open("w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(HEADER)
    done = load_completed()
    api = client()
    new_attempted = 0
    new_completed = 0

    conditions = CONDITIONS if args.condition == "all" else (args.condition,)
    with RESULTS.open("a", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        for condition in conditions:
            for repeat in range(1, args.repeats + 1):
                run = f"{condition}-{repeat:02d}"
                log_file = LOGS / f"{run}.txt"
                with log_file.open("a", encoding="utf-8") as capture:
                    def log(text: str) -> None:
                        print(text, flush=True)
                        capture.write(text + "\n")
                        capture.flush()

                    log(f"[RUN] {run} model={MODEL} temperature={TEMPERATURE} "
                        f"turn_limit={TURN_LIMIT} max_tokens={MAX_TOKENS}")
                    for scenario in scenarios:
                        key = (run, condition, str(scenario["id"]))
                        if key in done:
                            continue
                        new_attempted += 1
                        log(f"[SCENARIO] {scenario['id']} item={scenario['item']} "
                            f"reserve={scenario['reserve']} budget={scenario['budget']}")
                        try:
                            metrics = episode(api, scenario, condition, log)
                        except PauseForQuota:
                            note = "paused: HTTP 429 rate limit; partial episode; retry later"
                            log(f"[PAUSED] {note}")
                            writer.writerow([run, condition, scenario["id"]] + [""] * 8 + [note])
                            file.flush()
                            raise SystemExit(
                                "Stopped after rate limit. Partial row saved; "
                                "run this command again after quota recovery."
                            )
                        except Exception as exc:
                            # Do not echo exception messages: provider errors may
                            # contain tokens or sensitive request data.
                            note = f"crash: {type(exc).__name__}; episode may be retried"
                            # Log only source function names and line numbers, not
                            # exception messages or request contents (may contain keys).
                            locations = [
                                f"{frame.name}:{frame.lineno}"
                                for frame in traceback.extract_tb(exc.__traceback__)
                                if Path(frame.filename).name == "negotiation.py"
                            ]
                            log(f"[CRASH] {note} at {' > '.join(locations) or 'external API'}")
                            writer.writerow([run, condition, scenario["id"]] + [""] * 8 + [note])
                            file.flush()
                            if args.max_episodes and new_attempted >= args.max_episodes:
                                log("[CHECKPOINT] Attempt limit reached after failed episode; "
                                    "results.csv and logs/ saved. Fix the error or retry later.")
                                return
                            continue

                        row = [run, condition, scenario["id"],
                               metrics["deal_possible"], metrics["outcome"],
                               "" if metrics["price"] is None else metrics["price"],
                               metrics["correct"], metrics["violation"], metrics["turns"],
                               metrics["format_errors"], metrics["reader_calls"],
                               metrics["note"]]
                        writer.writerow(row)
                        file.flush()
                        done.add(key)
                        new_completed += 1
                        log(f"[RESULT] run={run} scenario={scenario['id']} "
                            f"outcome={metrics['outcome']} price={metrics['price']} "
                            f"correct={metrics['correct']} violation={metrics['violation']} "
                            f"turns={metrics['turns']} format_errors={metrics['format_errors']} "
                            f"reader_calls={metrics['reader_calls']}")
                        if args.max_episodes and new_attempted >= args.max_episodes:
                            print(f"[CHECKPOINT] {new_attempted} attempted episode(s), "
                                  f"{new_completed} completed. Re-run to continue; "
                                  "results.csv and logs/ are saved.")
                            return
    print("[DONE] All requested complete episodes are recorded.")


if __name__ == "__main__":
    main()
