#!/usr/bin/env python3
import csv, json, os, re, sys, time
from pathlib import Path
from urllib import request, error

BASE = Path(__file__).resolve().parent
SCENARIOS = BASE / "scenarios.json"
RESULTS = BASE / "results.csv"
LOGS = BASE / "logs"
REPORT = BASE / "REPORT.md"

CONDITIONS = ("free", "tagged", "structured")
PERFS = {"propose", "accept-proposal", "reject-proposal", "refuse"}
MAX_TURNS = int(os.getenv("MAX_TURNS", "8"))
REPEATS = int(os.getenv("REPEATS", "3"))
PROVIDER = "OpenRouter"
MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-lightning:free")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))
API_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
API_KEY = os.getenv("OPENROUTER_API_KEY", "")

HEADER = ["run","condition","scenario","deal_possible","outcome","price","correct",
          "violation","turns","format_errors","reader_calls","note"]

ROLE = {
    "buyer": (
        "You are the buyer of {item}, negotiating the price with the seller. "
        "Your private limit is {limit}: you can pay at most {limit}. Never agree to a price above {limit}. "
        "Your goal is to reach a deal if possible while paying no more than your limit."
    ),
    "seller": (
        "You are the seller of {item}, negotiating the price with the buyer. "
        "Your private limit is {limit}: you can accept at least {limit}. Never agree to a price below {limit}. "
        "Your goal is to reach a deal if possible while receiving no less than your limit."
    ),
}
COMMON = (
    " Four acts are available: propose (offer a price), accept-proposal (agree to the other side's last "
    "proposed price and end with a deal), reject-proposal (decline the last proposal and keep negotiating), "
    "and refuse (leave the negotiation permanently with no deal). Use only these four acts. "
    "When you accept-proposal, do not introduce a new price; you are accepting the other side's last proposal."
)
FORMAT = {
    "free": " Write your message as one or two plain English sentences. Do not use JSON or a leading performative tag.",
    "tagged": (
        " Start your message with exactly one performative tag in parentheses, one of (propose), "
        "(accept-proposal), (reject-proposal), (refuse), then write one plain English sentence."
    ),
    "structured": (
        ' Reply with exactly one JSON object and nothing else: {"performative": "propose" | '
        '"accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null>}}. '
        'For propose, price must be a whole number. For every other act, price must be null.'
    ),
}
READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only. Reply with exactly one JSON object and nothing else: "
    '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", '
    '"price": <whole number or null>}. '
    "Use propose only when the last message itself offers a new price. Use accept-proposal only when it explicitly "
    "agrees to the other side's previous offered price. Use reject-proposal when it declines but continues. "
    "Use refuse when it ends/leaves the negotiation. For propose, extract the NEW price offered by the speaker, "
    "not a price merely mentioned as the other side's earlier offer. For non-propose acts, price should be null."
)
PRICE_READER_SYSTEM = (
    "Read only the LAST negotiation message. It is known to be a propose. Extract the NEW whole-number price "
    "offered by the speaker. Reply with exactly one JSON object and nothing else: {\"price\": <whole number>}. "
    "If the sentence mentions both the other side's old price and a new counter-offer, return the new counter-offer."
)


def system_prompt(role, item, limit, condition):
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]


def call_model(system, history, attempts=8):
    if not API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    payload = {
        "model": MODEL,
        "temperature": TEMPERATURE,
        "messages": [{"role": "system", "content": system}] + history,
        "max_tokens": 220,
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Q00/ai-agent-engineering-101",
        "X-Title": "SeoulTech AI Agent Engineering Week 04",
    }
    delay = 2
    for attempt in range(attempts):
        try:
            req = request.Request(API_URL, data=data, headers=headers, method="POST")
            with request.urlopen(req, timeout=90) as r:
                obj = json.loads(r.read().decode("utf-8"))
            text = obj["choices"][0]["message"]["content"]
            if isinstance(text, list):
                text = "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in text)
            return str(text).strip()
        except error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code == 429 or 500 <= e.code < 600:
                time.sleep(delay); delay = min(delay * 2, 45); continue
            raise RuntimeError(f"HTTP {e.code}: {body[:500]}") from e
        except (error.URLError, TimeoutError) as e:
            if attempt == attempts - 1: raise
            time.sleep(delay); delay = min(delay * 2, 45)
    raise RuntimeError("model call failed after retries")


def parse_json_object(text):
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m: return None
        try: return json.loads(m.group(0))
        except Exception: return None


def reader_full(transcript, last_text):
    # Give the reader the full dialogue for context, but instruct it to label only the last message.
    rendered = "\n".join(f"[{r}] {t}" for r, t in transcript)
    hist = [{"role":"user", "content": rendered + "\n\nLAST MESSAGE:\n" + last_text}]
    raw = call_model(READER_SYSTEM, hist)
    obj = parse_json_object(raw)
    if not isinstance(obj, dict): return None, None, False, raw
    perf, price = obj.get("performative"), obj.get("price")
    if perf not in PERFS: return None, None, False, raw
    if perf == "propose":
        if isinstance(price, bool) or not isinstance(price, int): return None, None, False, raw
    else:
        price = None
    return perf, price, True, raw


def reader_price(text):
    raw = call_model(PRICE_READER_SYSTEM, [{"role":"user", "content":text}])
    obj = parse_json_object(raw)
    price = obj.get("price") if isinstance(obj, dict) else None
    ok = isinstance(price, int) and not isinstance(price, bool)
    return (price if ok else None), ok, raw


def read_message(condition, text, transcript):
    if condition == "free":
        perf, price, ok, raw = reader_full(transcript, text)
        return perf, price, ok, 1, f"[reader] {raw}"
    if condition == "tagged":
        m = re.match(r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)\s*(.*)$", text, flags=re.S)
        if not m:
            return None, None, False, 0, "[parser] invalid or missing leading performative tag"
        perf, body = m.group(1), m.group(2)
        if perf == "propose":
            price, ok, raw = reader_price(body)
            return perf, price, ok, 1, f"[reader-price] {raw}"
        return perf, None, True, 0, f"[parser] performative={perf}"
    obj = parse_json_object(text)
    # structured must be exactly one JSON object: any leading/trailing prose is an error.
    if not isinstance(obj, dict) or text.strip() != json.dumps(obj, separators=(",", ":"), ensure_ascii=False) and not _json_equivalent_exact(text, obj):
        return None, None, False, 0, "[parser] structured message is not exactly one JSON object"
    perf = obj.get("performative")
    content = obj.get("content")
    if perf not in PERFS or not isinstance(content, dict):
        return None, None, False, 0, "[parser] invalid performative/content"
    price = content.get("price")
    if perf == "propose":
        if isinstance(price, bool) or not isinstance(price, int):
            return None, None, False, 0, "[parser] propose missing integer content.price"
    else:
        if price is not None:
            return None, None, False, 0, "[parser] non-propose must use null content.price"
        price = None
    return perf, price, True, 0, f"[parser] performative={perf} price={price}"


def _json_equivalent_exact(text, obj):
    # Accept arbitrary JSON whitespace but reject prose around it.
    try:
        dec = json.JSONDecoder()
        parsed, end = dec.raw_decode(text.lstrip())
        tail = text.lstrip()[end:].strip()
        return not tail and parsed == obj
    except Exception:
        return False


def load_scenarios():
    return json.loads(SCENARIOS.read_text(encoding="utf-8"))


def load_done():
    done = set()
    if not RESULTS.exists(): return done
    with RESULTS.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("run") and row.get("scenario"):
                done.add((row["run"], str(row["scenario"])))
    return done


def append_result(row):
    exists = RESULTS.exists() and RESULTS.stat().st_size > 0
    with RESULTS.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        if not exists: w.writeheader()
        w.writerow(row)


def run_episode(sc, condition, log):
    systems = {
        "buyer": system_prompt("buyer", sc["item"], sc["budget"], condition),
        "seller": system_prompt("seller", sc["item"], sc["reserve"], condition),
    }
    history = {"buyer": [], "seller": []}
    transcript = []
    last_price = {"buyer": None, "seller": None}
    role, other = "buyer", "seller"
    outcome, deal_price = "open", None
    turns = format_errors = reader_calls = 0

    for _ in range(MAX_TURNS):
        text = call_model(systems[role], history[role])
        turns += 1
        history[role].append({"role":"assistant", "content":text})
        history[other].append({"role":"user", "content":text})
        transcript.append((role, text))
        log.append(f"[{role}] {text}")
        perf, price, ok, rc, detail = read_message(condition, text, transcript)
        reader_calls += rc
        log.append(detail)
        if not ok:
            format_errors += 1
        elif perf == "propose":
            last_price[role] = price
        elif perf == "accept-proposal":
            if last_price[other] is None:
                format_errors += 1
                log.append("[protocol] accept-proposal but other side has no recorded proposal; continue")
            else:
                outcome, deal_price = "deal", last_price[other]
                break
        elif perf == "refuse":
            outcome = "no_deal"
            break
        role, other = other, role

    deal_possible = int(sc["reserve"] <= sc["budget"])
    violation = int(outcome == "deal" and (deal_price < sc["reserve"] or deal_price > sc["budget"]))
    if outcome == "deal":
        correct = int(bool(deal_possible) and not violation)
    elif outcome == "no_deal":
        correct = int(not deal_possible)
    else:
        correct = 0
    return {
        "deal_possible": deal_possible, "outcome": outcome, "price": "" if deal_price is None else deal_price,
        "correct": correct, "violation": violation, "turns": turns,
        "format_errors": format_errors, "reader_calls": reader_calls,
    }


def build_report():
    rows = []
    if RESULTS.exists():
        with RESULTS.open(encoding="utf-8", newline="") as f: rows = list(csv.DictReader(f))
    summaries = {}
    for c in CONDITIONS:
        rr = [r for r in rows if r["condition"] == c]
        def isum(k): return sum(int(r[k] or 0) for r in rr)
        summaries[c] = {
            "n": len(rr), "correct": isum("correct"), "violation": isum("violation"),
            "turns": (sum(int(r["turns"] or 0) for r in rr)/len(rr) if rr else 0),
            "format_errors": isum("format_errors"), "reader_calls": isum("reader_calls")
        }

    completed = len(rows) >= len(load_scenarios()) * len(CONDITIONS) * REPEATS
    lines = [
        "# Week 04 — Communication Languages: From Speech Acts to FIPA-ACL", "",
        "## 1. Setup", "",
        f"Provider: **{PROVIDER}**; model: **`{MODEL}`**; temperature: **{TEMPERATURE}**; turn limit: **{MAX_TURNS}**. "
        "The buyer and seller share the same role/limit rules and four-act vocabulary; only the message-format paragraph changes.", "",
        f"- **free:** {FORMAT['free']}",
        f"- **tagged:** {FORMAT['tagged']}",
        f"- **structured:** {FORMAT['structured']}", "",
        f"Reader prompt: `{READER_SYSTEM}`", "",
        "Run with `OPENROUTER_API_KEY=... python run_week04.py`. The program retries 429/5xx errors, resumes already completed `(run, scenario)` pairs, writes one log per condition/repeat, and regenerates this report.", "",
        "## 2. Results", "",
        "| condition | correct | violation | mean turns | format_errors | reader_calls |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for c in CONDITIONS:
        s=summaries[c]; lines.append(f"| {c} | {s['correct']}/{s['n']} | {s['violation']} | {s['turns']:.2f} | {s['format_errors']} | {s['reader_calls']} |")
    lines += ["", "### Every episode from `results.csv`", "",
              "| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |",
              "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|"]
    for r in rows:
        vals=[r[h].replace("|","/") for h in HEADER]
        lines.append("| " + " | ".join(vals) + " |")
    if not rows: lines.append("| _No episodes yet — run `python run_week04.py`_ |  |  |  |  |  |  |  |  |  |  |  |")

    lines += ["", "## 3. FIPA-ACL comparison", "",
        "| item | FIPA-ACL | free | tagged | structured |",
        "|---|---|---|---|---|",
        "| Illocutionary force | Explicit ACL performative field | In natural-language wording; inferred by reader | Explicit leading tag | Explicit `performative` JSON field |",
        "| Content language | Declared content language/ontology can be specified | Plain English | Plain English after tag | JSON object with typed price field |",
        "| Who interprets content | Agent/content-language interpreter using agreed semantics | LLM reader | Regex for act; LLM reader for proposal price | Deterministic JSON parser |",
        "| Conversation end | Defined by interaction protocol / performative semantics | Reader-inferred `accept-proposal` or `refuse`, or 8-turn limit | Parsed tag ends or 8-turn limit | Parsed JSON act ends or 8-turn limit |",
        "| Sincerity guarantee | Semantic preconditions/rational-effect conventions are normative, not mechanically guaranteed | System prompt only; model may violate it | Same system-prompt constraint | Same system-prompt constraint |",
        "| Cost to read one message | Parser/interpreter cost; no LLM inherently required | One LLM reader call per message | Regex; plus one reader call for each `propose` price | Deterministic parse, zero reader calls |",
        "| Typical failure | Ontology/semantic mismatch or agent violates communicative assumptions | Ambiguous act/price, reader misclassification | Correct tag can hide a counter-offer in prose; malformed/missing tags | Invalid JSON, prose outside JSON, null/wrong typed price, or agent limit violation |",
        "", "## 4. Interpretation", ""]

    if completed:
        # Find a few objective log excerpts for evidence.
        evidence=[]
        for p in sorted(LOGS.glob("*.txt")):
            txt=p.read_text(encoding="utf-8", errors="ignore").splitlines()
            for i,line in enumerate(txt):
                low=line.lower()
                if "invalid" in low or "format_errors=" in low or "violation=1" in low or "accept-proposal but" in low:
                    evidence.append((p.name, line.strip()))
                    if len(evidence)>=3: break
            if len(evidence)>=3: break
        fs=summaries['free']; ts=summaries['tagged']; ss=summaries['structured']
        paragraph=(
            f"Across {len(rows)} episodes, changing only the message representation changed both protocol cost and failure mode. "
            f"Free required {fs['reader_calls']} reader calls and produced {fs['format_errors']} format errors; tagged reduced act interpretation to a regex but still used {ts['reader_calls']} reader calls to recover proposal prices; structured used {ss['reader_calls']} reader calls because both act and price were machine-readable. "
            f"Correct outcomes were free {fs['correct']}/{fs['n']}, tagged {ts['correct']}/{ts['n']}, and structured {ss['correct']}/{ss['n']}; violations were {fs['violation']}, {ts['violation']}, and {ss['violation']} respectively. "
            "This shows that an explicit performative removes one ambiguity, but it does not guarantee rational behavior or faithful content generation: the agents can still violate private limits, place a counter-offer under the wrong act, or emit malformed structure."
        )
        if evidence:
            quotes=" ".join(f"`{fn}` records `{ln[:120]}`." for fn,ln in evidence)
            paragraph += " Evidence from the logs: " + quotes
        lines.append(paragraph)
    else:
        lines.append("Run the experiment first. This paragraph is generated from the measured numbers and log excerpts so no result is fabricated before the API run.")
    REPORT.write_text("\n".join(lines)+"\n", encoding="utf-8")


def main():
    LOGS.mkdir(exist_ok=True)
    scenarios=load_scenarios(); done=load_done()
    # ensure report exists even before model calls
    build_report()
    if not API_KEY:
        print("ERROR: OPENROUTER_API_KEY is not set. Set it in your shell, then rerun.")
        print(f"Example: export OPENROUTER_API_KEY='your_key_here'   # macOS/Linux/Git Bash")
        print(f"PowerShell: $env:OPENROUTER_API_KEY='your_key_here'")
        return 2
    for condition in CONDITIONS:
        for rep in range(1, REPEATS+1):
            run_id=f"{condition}-{rep:02d}"
            log_path=LOGS/f"{run_id}.txt"
            existing = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
            header=(f"provider={PROVIDER} model={MODEL} temperature={TEMPERATURE} max_turns={MAX_TURNS} "
                    f"condition={condition} run={run_id}\n")
            if not existing: log_path.write_text(header, encoding="utf-8")
            for sc in scenarios:
                key=(run_id,str(sc['id']))
                if key in done:
                    print("skip", key); continue
                log=[f"\n=== scenario {sc['id']}: {sc['item']} reserve={sc['reserve']} budget={sc['budget']} ==="]
                note=""
                try:
                    ep=run_episode(sc,condition,log)
                except Exception as e:
                    ep={"deal_possible":int(sc['reserve']<=sc['budget']),"outcome":"","price":"","correct":"",
                        "violation":"","turns":"","format_errors":"","reader_calls":""}
                    note=f"CRASH: {type(e).__name__}: {str(e)[:220]}"
                    log.append("[crash] "+note)
                row={"run":run_id,"condition":condition,"scenario":str(sc['id']),**ep,"note":note}
                append_result(row); done.add(key)
                log.append("[result] "+" ".join(f"{k}={row[k]}" for k in ["outcome","price","correct","violation","turns","format_errors","reader_calls"]))
                with log_path.open("a",encoding="utf-8") as f: f.write("\n".join(log)+"\n")
                print(run_id, sc['id'], row['outcome'], "correct=",row['correct'])
                build_report()
    build_report()
    print(f"Done. Results: {RESULTS}")
    print(f"Report:  {REPORT}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
