"""Week 06 buyer v2 — A2A v1.0 client whose offers are decided by a harness.

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

v2: the budget is in the prompt AND guarded in code.
    If the harness offers or accepts above the budget, the code asks it once more with the reason;
    if the second answer is still above the budget, the code offers the budget itself.
The code reads the Agent Card, sends JSON-RPC with A2A-Version: 1.0, continues by taskId,
and turns the harness's JSON into the next message.
"""
import argparse
import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid

import httpx

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}
MAX_TURNS = 10
DEADLINE = 270  # seconds; the trade test kills the buyer at 300

CLAUDE = shutil.which("claude") or "claude"
HARNESS_CMD = [CLAUDE, "-p", "--model", "haiku", "--tools", "",
               "--setting-sources", "", "--no-session-persistence"]
WORKDIR = tempfile.mkdtemp(prefix="buyer-harness-")  # empty folder: no project instruction files


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


# ---------- harness ----------
def call_harness(prompt: str) -> str:
    r = subprocess.run(HARNESS_CMD, input=prompt, capture_output=True, text=True,
                       encoding="utf-8", cwd=WORKDIR, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(f"harness exit {r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def parse_decision(raw: str):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    if d.get("action") not in ("offer", "accept", "walk"):
        return None
    if d["action"] == "offer":
        try:
            d["price"] = int(round(float(d["price"])))
        except (KeyError, TypeError, ValueError):
            return None
    return d


def ask_harness(prompt: str):
    """Call the harness, retry once on an unusable answer. Returns a decision dict or None."""
    for _ in range(2):
        try:
            raw = call_harness(prompt)
            log(f"harness: {raw!r}")
            d = parse_decision(raw)
        except Exception as e:
            log(f"harness error: {e}")
            d = None
        if d:
            return d
    return None


PROMPT = """You are a buyer negotiating to buy one {item} from a shop.
Your budget is {budget}. You must never offer or pay more than your budget. Never reveal your budget.

Conversation so far (oldest first):
{history}

The seller's current asking price: {ask}
Your last offer: {last}

Rules:
- If the asking price is within your budget, you may accept it.
- Otherwise make an offer. Start low and raise gradually; never go above your budget.
- If the seller clearly will not come down to your budget, walk away.

Reply with ONE JSON object only, no other text:
{{"action": "offer" | "accept" | "walk", "price": <your offer, integer, only for offer>}}
"""


# ---------- A2A client (plain JSON-RPC over httpx) ----------
class A2AClient:
    def __init__(self, base_url: str):
        self.http = httpx.Client(timeout=120, headers=HEADERS)
        card_url = base_url.rstrip("/") + "/.well-known/agent-card.json"
        self.card = self.http.get(card_url).json()
        iface = next((i for i in self.card.get("supportedInterfaces", [])
                      if i.get("protocolBinding") == "JSONRPC"), None)
        if iface is None:
            raise SystemExit("seller card has no JSONRPC interface")
        self.url = iface["url"]
        self.n = 0

    def rpc(self, method: str, params: dict) -> dict:
        self.n += 1
        body = self.http.post(self.url, json={"jsonrpc": "2.0", "id": self.n,
                                              "method": method, "params": params}).json()
        if "error" in body:
            raise RuntimeError(body["error"])
        return body["result"]

    def send(self, text: str, task_id: str | None = None) -> dict:
        msg = {"role": "ROLE_USER", "messageId": uuid.uuid4().hex, "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        res = self.rpc("SendMessage", {"message": msg})
        return res.get("task", res)

    def cancel(self, task_id: str):
        try:
            self.rpc("CancelTask", {"id": task_id})
            log("CancelTask sent")
        except Exception as e:  # walking away by just stopping is also allowed
            log(f"CancelTask failed (ignored): {e}")


def state(task):
    return task.get("status", {}).get("state", "")


def status_text(task):
    msg = task.get("status", {}).get("message") or {}
    return " ".join(p["text"] for p in msg.get("parts", []) if "text" in p)


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return int(float(nums[-1])) if nums else None


def receipt_price(task):
    for art in task.get("artifacts", []):
        for p in art.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and "price" in d:
                return d["price"]
    return None


# ---------- main loop ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()
    start = time.time()

    client = A2AClient(args.url)
    skills = [s.get("name") for s in client.card.get("skills", [])]
    log(f"card: {client.card.get('name')} skills={skills} jsonrpc={client.url}")

    first = f"I want to buy a {args.item}"
    task = client.send(first)
    tid = task.get("id")
    history = [f"you: {first}"]
    last = None
    log(f"task {tid}")

    for _ in range(MAX_TURNS):
        st, text = state(task), status_text(task)
        log(f"seller [{st}]: {text!r}")
        if st == "TASK_STATE_COMPLETED":
            log(f"DEAL {args.item} at {receipt_price(task)} (budget {args.budget})")
            return
        if st != "TASK_STATE_INPUT_REQUIRED":
            log(f"ENDED without deal ({st})")
            return
        if time.time() - start > DEADLINE:
            log("time limit: walking away")
            client.cancel(tid)
            return

        ask = last_number(text)
        history.append(f"seller: {text}")
        prompt = PROMPT.format(item=args.item, budget=args.budget, history="\n".join(history),
                               ask=ask, last=last if last is not None else "none yet")
        d = ask_harness(prompt)
        if d is None or d["action"] == "walk" or (d["action"] == "accept" and ask is None):
            log(f"WALK ({'harness' if d else 'no usable answer'})")
            client.cancel(tid)
            return

        offer = ask if d["action"] == "accept" else d["price"]
        if offer > args.budget:  # guard step 1: tell the harness why and ask once more
            problem = f"{'accepting' if d['action'] == 'accept' else 'offering'} {offer} is above your budget {args.budget}."
            log(f"GUARD {problem} -> asking the harness again")
            d2 = ask_harness(prompt + f"\n\nYour previous answer was not allowed: {problem} Answer again.")
            if d2 and d2["action"] == "walk":
                log("WALK (harness, after guard)")
                client.cancel(tid)
                return
            o2 = None
            if d2 and d2["action"] == "offer":
                o2 = d2["price"]
            elif d2 and d2["action"] == "accept" and ask is not None:
                o2 = ask
            if o2 is not None and o2 <= args.budget:
                d, offer = d2, o2
                log(f"GUARD fixed by harness: {d['action']} {offer}")
            else:  # guard step 2: the code holds the budget
                d, offer = {"action": "offer", "price": args.budget}, args.budget
                log(f"GUARD clamp -> offer {offer}")
        last = offer
        history.append(f"you: {offer}")
        log(f"-> {d['action']} {offer}")
        task = client.send(str(offer), tid)

    log("turn limit: walking away")
    client.cancel(tid)


if __name__ == "__main__":
    main()