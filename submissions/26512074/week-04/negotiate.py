import csv
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from openai import OpenAI, RateLimitError

from acl import READER_SYSTEM, parse_structured, parse_tag, system_prompt

BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("AGENT_MODEL")
TEMPERATURE = float(os.getenv("AGENT_TEMPERATURE", "0.2"))
MAX_TURNS = int(os.getenv("MAX_TURNS", "8"))
RETRY_LIMIT = int(os.getenv("RATE_LIMIT_RETRIES", "5"))

if not API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY is not set. Set your OpenRouter key in the environment."
    )
if not MODEL:
    raise RuntimeError(
        "AGENT_MODEL is not set. Use the model name assigned for this experiment."
    )

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

if BASE_URL.startswith("https://openrouter.ai/"):
    DEFAULT_EXTRA_BODY = {"reasoning": {"enabled": False}}
else:
    DEFAULT_EXTRA_BODY = {}


@dataclass
class Meter:
    reader_calls: int = 0
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add_usage(self, usage: object) -> None:
        if usage is None:
            return
        self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)


@dataclass
class Episode:
    run: int
    condition: str
    scenario_id: str
    deal_possible: int
    outcome: str = ""
    price: Optional[int] = None
    correct: Optional[int] = None
    violation: Optional[int] = None
    turns: int = 0
    format_errors: int = 0
    meter: Meter = field(default_factory=Meter)
    note: str = ""


def call_model(messages: list[dict[str, str]], meter: Meter, temperature: float = TEMPERATURE) -> str:
    for attempt in range(RETRY_LIMIT + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=1024,
                extra_body=DEFAULT_EXTRA_BODY,
            )
            meter.model_calls += 1
            meter.add_usage(response.usage)
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError("model returned empty content")
            return content.strip()
        except RateLimitError as exc:
            if attempt >= RETRY_LIMIT:
                raise RuntimeError("HTTP 429 persisted after retries") from exc
            wait = min(30, 2 ** attempt)
            print(f"[retry] HTTP 429; sleeping {wait}s")
            time.sleep(wait)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status == 429 and attempt < RETRY_LIMIT:
                wait = min(30, 2 ** attempt)
                print(f"[retry] HTTP 429; sleeping {wait}s")
                time.sleep(wait)
                continue
            raise


def render_transcript(transcript: list[dict[str, str]]) -> str:
    lines = []
    for message in transcript:
        lines.append(f"[{message['speaker']}] {message['content']}")
    return "\n".join(lines)


def read_message(
    condition: str,
    text: str,
    transcript: list[dict[str, str]],
    meter: Meter,
    log: Callable[[str], None],
) -> tuple[Optional[str], Optional[int], bool]:
    if condition == "structured":
        perf, price, ok, reason = parse_structured(text)
        log(f"[parser] performative={perf!r} price={price!r} ok={ok} reason={reason}")
        return perf, price, ok

    if condition == "tagged":
        perf, tag_ok, reason = parse_tag(text)
        if not tag_ok:
            log(f"[regex] performative=None ok=False reason={reason}")
            return None, None, False
        if perf != "propose":
            log(f"[regex] performative={perf!r} ok=True")
            return perf, None, True

        reader_messages = [
            {"role": "system", "content": READER_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Read this negotiation transcript and label the LAST message.\n\n"
                    + render_transcript(transcript)
                ),
            },
        ]
        meter.reader_calls += 1
        raw = call_model(reader_messages, meter)
        try:
            parsed = json.loads(raw)
            price = parsed.get("price")
            ok = _valid_reader_price(price)
        except json.JSONDecodeError:
            price = None
            ok = False
        log(
            f"[reader] raw={raw!r} performative(from tag)={perf!r} "
            f"price={price!r} ok={ok}"
        )
        return perf, price if ok else None, ok

    if condition == "free":
        reader_messages = [
            {"role": "system", "content": READER_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Read this negotiation transcript and label the LAST message.\n\n"
                    + render_transcript(transcript)
                ),
            },
        ]
        meter.reader_calls += 1
        raw = call_model(reader_messages, meter)
        try:
            parsed = json.loads(raw)
            perf = parsed.get("performative")
            price = parsed.get("price")
            ok = (
                perf in {"propose", "accept-proposal", "reject-proposal", "refuse"}
                and (price is None or _valid_reader_price(price))
                and not (perf == "propose" and price is None)
            )
        except json.JSONDecodeError:
            perf, price, ok = None, None, False
        log(f"[reader] raw={raw!r} performative={perf!r} price={price!r} ok={ok}")
        return perf, price, ok

    raise ValueError(f"unknown condition: {condition}")


def _valid_reader_price(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def negotiate_episode(
    scenario: dict,
    condition: str,
    run: int,
    log: Callable[[str], None],
) -> Episode:
    buyer_limit = int(scenario["budget"])
    seller_limit = int(scenario["reserve"])
    deal_possible = int(seller_limit <= buyer_limit)
    ep = Episode(
        run=run,
        condition=condition,
        scenario_id=str(scenario["id"]),
        deal_possible=deal_possible,
    )

    systems = {
        "buyer": system_prompt(
            "buyer",
            scenario["item"],
            buyer_limit,
            condition,
            int(scenario["id"]),
            scenario.get("twist"),
        ),
        "seller": system_prompt(
            "seller",
            scenario["item"],
            seller_limit,
            condition,
            int(scenario["id"]),
            scenario.get("twist"),
        ),
    }
    history = {"buyer": [], "seller": []}
    transcript: list[dict[str, str]] = []
    last_price: dict[str, Optional[int]] = {"buyer": None, "seller": None}

    role, other = "buyer", "seller"

    log(
        f"[episode] run={run} condition={condition} scenario={scenario['id']} "
        f"item={scenario['item']!r} reserve={seller_limit} budget={buyer_limit}"
    )

    for turn_index in range(MAX_TURNS):
        messages = history[role]
        messages_for_model = [{"role": "system", "content": systems[role]}] + messages
        text = call_model(messages_for_model, ep.meter)

        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        transcript.append({"speaker": role, "content": text})
        ep.turns += 1
        log(f"[{role}] {text}")

        perf, price, ok = read_message(
            condition, text, transcript, ep.meter, log
        )
        if not ok:
            ep.format_errors += 1
            log("[protocol] format_errors += 1")

        if ok and perf == "propose":
            last_price[role] = price
            log(f"[state] last_price[{role}]={price}")
        elif ok and perf == "accept-proposal":
            accepted = last_price[other]
            if accepted is None:
                ep.format_errors += 1
                ep.note = "accept-proposal had no recorded price from the other side"
                log(f"[protocol] {ep.note}")
                ep.outcome = "no_deal"
                break
            ep.outcome = "deal"
            ep.price = accepted
            log(f"[result] deal price={accepted}")
            break
        elif ok and perf == "refuse":
            ep.outcome = "no_deal"
            log("[result] no_deal via refuse")
            break

        role, other = other, role

    if not ep.outcome:
        ep.outcome = "open"
        log("[result] open: max turn limit reached")

    if ep.outcome == "deal" and ep.price is not None:
        ep.violation = int(ep.price < seller_limit or ep.price > buyer_limit)
    else:
        ep.violation = 0

    if ep.outcome == "deal":
        ep.correct = int(deal_possible == 1 and ep.violation == 0)
    elif ep.outcome == "no_deal":
        ep.correct = int(deal_possible == 0)
    else:
        ep.correct = 0

    if not ep.note:
        ep.note = (
            f"model_calls={ep.meter.model_calls}; "
            f"prompt_tokens={ep.meter.prompt_tokens}; "
            f"completion_tokens={ep.meter.completion_tokens}"
        )

    return ep


def ensure_results(path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    header = [
        "run", "condition", "scenario", "deal_possible", "outcome", "price",
        "correct", "violation", "turns", "format_errors", "reader_calls", "note"
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(header)


def existing_pairs(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as f:
        rows = csv.DictReader(f)
        return {
            (row["run"], row["condition"], row["scenario"])
            for row in rows
            if row.get("run") and row.get("condition") and row.get("scenario")
        }


def append_result(path: Path, ep: Episode) -> None:
    with path.open("a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow([
            ep.run,
            ep.condition,
            ep.scenario_id,
            ep.deal_possible if ep.deal_possible is not None else "",
            ep.outcome,
            ep.price if ep.price is not None else "",
            ep.correct if ep.correct is not None else "",
            ep.violation if ep.violation is not None else "",
            ep.turns,
            ep.format_errors,
            ep.meter.reader_calls,
            ep.note,
        ])
