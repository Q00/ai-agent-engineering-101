"""LongMemEval-V2 style check of the fast memory tier (human code, not editable by the model).

    python3 -m kernel.memcheck --root <data root> [--n 3] [--top-k 10]

Five abilities, questions and ground truth computed by plain code from the same logs the feeder reads:
  static    a fact that happened once (our only settle price of a SKU)
  dynamic   the latest value of something that changed (most recent settle price of a SKU sold at several prices)
  workflow  a count inside a procedure (offers our buyer sent before a deal at a hashed shop)
  gotcha    what went wrong (the reason a buyer attempt failed)
  premise   a question about a SKU that never appeared; the right answer is 모름/없음
Each question: search the daemon (top_k), then one model call that reads only the retrieved memories. Scoring is
simple: number within tolerance, string contained, or abstention. At most n questions per ability. Result goes to
<root>/a2/memcheck.json. The memory and model are injected callables, so tests use fakes. Stdlib only.
"""

import argparse
import hashlib
import json
import os
import re

from kernel import common as c
from kernel.agent import ParseError, parse_last_object
from kernel.llm import ModelError, ModelRefused

ABILITIES = ("static", "dynamic", "workflow", "gotcha", "premise")
ABSTAIN = ("모름", "없음", "알 수 없")
REL_TOL = 0.01
MAX_EXAMPLES = 3
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _settles(rows, login=""):
    """{sku: [(ts, amount), ...] oldest first} for our own settles."""
    out = {}
    for e in rows:
        d = e.get("data") or {}
        if (
            e.get("type") != "settle"
            or not isinstance(d, dict)
            or c.settle_side(d, login) != "shop"
        ):
            continue
        a = d.get("amount")
        if isinstance(d.get("sku"), str) and isinstance(a, (int, float)):
            out.setdefault(d["sku"], []).append((e.get("ts") or 0, a))
    for v in out.values():
        v.sort()
    return out


def build_questions(traj_rows, settle_rows, n=3, login=""):
    """Question dicts (ability, agent, question, query, check, truth), the n most recent per ability."""
    qs = {a: [] for a in ABILITIES}
    sett = _settles(settle_rows, login)
    by_recent = sorted(sett.items(), key=lambda kv: -kv[1][-1][0])
    for sku, hist in by_recent:
        query = f"우리 가게 판매 정산 SKU {sku} 금액"
        if len(hist) == 1:
            qs["static"].append(
                {
                    "agent": "seller",
                    "question": f"우리 가게가 SKU {sku}를 판 정산 금액은 얼마인가?",
                    "query": query,
                    "check": "number",
                    "truth": hist[0][1],
                }
            )
        elif any(a != hist[-1][1] for _, a in hist[:-1]):
            qs["dynamic"].append(
                {
                    "agent": "seller",
                    "question": f"우리 가게가 SKU {sku}를 여러 번 팔았다. 가장 최근 판매의 정산 금액은 얼마인가?",
                    "query": query,
                    "check": "number",
                    "truth": hist[-1][1],
                }
            )
    attempts = [
        r
        for r in traj_rows
        if r.get("kind") == "buyer_exec" and r.get("event") == "attempt"
    ]
    for r in sorted(attempts, key=lambda r: -(r.get("ts") or 0)):
        order, shop = r.get("order"), r.get("shop")
        query = f"구매 시도 주문 {order} 가게 {shop}"
        if r.get("result") == "accepted":
            qs["workflow"].append(
                {
                    "agent": "buyer",
                    "question": f"주문 {order}에서 가게 {shop}와 거래가 성사될 때까지 우리 구매자가 그 가게에 보낸 가격 제안은 몇 번인가?",
                    "query": query,
                    "check": "number",
                    "truth": len(r.get("offers_sent") or []),
                }
            )
        elif r.get("why") or r.get("result"):
            qs["gotcha"].append(
                {
                    "agent": "buyer",
                    "question": f"주문 {order}에서 가게 {shop}에 대한 구매 시도는 어떤 사유로 끝났나?",
                    "query": query,
                    "check": "contains",
                    "truth": str(r.get("why") or r.get("result")),
                }
            )
    seen = json.dumps([traj_rows, settle_rows], ensure_ascii=False, default=str)
    i = 0
    while len(qs["premise"]) < n:
        fake = "ZQ" + hashlib.sha1(f"premise-{i}".encode()).hexdigest()[:5].upper()
        i += 1
        if fake in seen:
            continue
        qs["premise"].append(
            {
                "agent": "seller",
                "question": f"우리 가게가 SKU {fake}를 판 정산 금액은 얼마인가?",
                "query": f"우리 가게 판매 정산 SKU {fake} 금액",
                "check": "abstain",
                "truth": "모름",
            }
        )
    return [dict(q, ability=a) for a in ABILITIES for q in qs[a][:n]]


def score(q, answer):
    answer = str(answer or "")
    if q["check"] == "abstain":
        return any(w in answer for w in ABSTAIN)
    if q["check"] == "contains":
        return q["truth"].lower() in answer.lower()
    nums = _NUM.findall(answer.replace(",", ""))
    if not nums:
        return False
    t = float(q["truth"])
    return abs(float(nums[0]) - t) <= max(REL_TOL * abs(t), 0.5)


def prompt_for(q, memories):
    mem = "\n".join(f"- {m}" for m in memories) or "(없음)"
    return (
        "아래 기억만 근거로 질문에 답하라. 기억에 답이 없으면 answer를 모름으로 하라. "
        "답이 수이면 수 하나만 적어라.\n\n"
        f"## 기억\n{mem}\n\n## 질문\n{q['question']}\n\n"
        '{"answer": "<짧은 답>"} 형식의 JSON 객체 하나만 출력'
    )


def run(questions, ask_memory, ask_model, top_k=10):
    """ask_memory(agent, query, top_k) -> [str]; ask_model(prompt) -> str. Returns the per-ability result."""
    res = {a: {"n": 0, "correct": 0, "examples": []} for a in ABILITIES}
    for q in questions:
        mems = ask_memory(q["agent"], q["query"], top_k) or []
        try:
            out = ask_model(prompt_for(q, mems))
            try:
                answer = str(parse_last_object(out).get("answer", ""))
            except ParseError:
                answer = out.strip()
        except (ModelError, ModelRefused) as e:
            answer = f"[{type(e).__name__}]"
        ok = score(q, answer)
        r = res[q["ability"]]
        r["n"] += 1
        r["correct"] += ok
        if len(r["examples"]) < MAX_EXAMPLES:
            r["examples"].append(
                {
                    "question": q["question"],
                    "truth": q["truth"],
                    "answer": answer[:200],
                    "ok": ok,
                    "n_memories": len(mems),
                }
            )
    return res


def load_rows(root):
    traj = list(
        c.tail_json(
            os.path.join(root, "a2/trajectory.jsonl"), 50_000_000, b'"buyer_exec"'
        )
    )
    settles = list(
        c.tail_json(os.path.join(root, ".r3/events.jsonl"), 40_000_000, b'"settle"')
    )
    return traj, settles


def write(root, res, n, top_k, now=None):
    login = c.owner_login(root)
    path = os.path.join(root, "a2/memcheck.json")
    doc = {
        "ts": round(c.now_ts() if now is None else now, 3),
        "n_per_ability": n,
        "top_k": top_k,
        "abilities": res,
    }
    text = c.mask(json.dumps(doc, ensure_ascii=False, indent=1), (login,))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


FIXTURE_TRAJ = [
    {
        "ts": 1_794_000_100,
        "kind": "buyer_exec",
        "event": "attempt",
        "order": "o1",
        "shop": "h1a2b3c",
        "sku": "BED1",
        "max_pay": 900,
        "offers_sent": [600, 700, 800],
        "final_ask": 800,
        "result": "accepted",
    },
    {
        "ts": 1_794_000_200,
        "kind": "buyer_exec",
        "event": "attempt",
        "order": "o2",
        "shop": "h4d5e6f",
        "sku": "EGG1",
        "max_pay": 40,
        "offers_sent": [],
        "final_ask": None,
        "result": "error",
        "why": "no endpoint",
    },
]
FIXTURE_SETTLE = [
    {
        "ts": 1_794_000_000,
        "type": "settle",
        "src": "market",
        "data": {"sku": "SOFA1", "amount": 700_000, "own_owner": True},
    },
    {
        "ts": 1_794_000_010,
        "type": "settle",
        "src": "market",
        "data": {"sku": "BED1", "amount": 900_000, "own_owner": True},
    },
    {
        "ts": 1_794_000_020,
        "type": "settle",
        "src": "market",
        "data": {"sku": "BED1", "amount": 950_000, "own_owner": True},
    },
    {
        "ts": 1_794_000_030,
        "type": "settle",
        "src": "market",
        "data": {"sku": "LAMP1", "amount": 50_000, "own_owner": False},
    },
]


def build_questions_for_test(n=3):
    """Questions from the fixed fixture rows above: at least one per ability."""
    return build_questions(FIXTURE_TRAJ, FIXTURE_SETTLE, n)


def main(argv=None):
    from kernel import memory
    from kernel.llm import Terra
    from kernel.replay import CLI
    from kernel.trajectory import Trajectory

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None)
    ap.add_argument("--sock", default=None)
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--cli", default=CLI)
    a = ap.parse_args(argv)
    root = c.data_root(a.root)
    login = c.owner_login(root)
    secrets = (login,) if login else ()
    terra = Terra(
        Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets),
        cli=a.cli,
        secrets=secrets,
    )
    sock = a.sock or memory.sock_path(root)
    questions = build_questions(*load_rows(root), n=a.n, login=login)
    res = run(
        questions,
        lambda agent, query, k: memory.search(sock, agent, query, k, timeout=5.0),
        lambda prompt: terra.complete(
            prompt, agent="operator", timeout_s=90, effort="low"
        ),
        top_k=a.top_k,
    )
    print(write(root, res, a.n, a.top_k))
    print(
        json.dumps(
            {k: f"{v['correct']}/{v['n']}" for k, v in res.items()}, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
