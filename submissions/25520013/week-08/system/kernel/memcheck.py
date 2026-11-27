"""LongMemEval-V2 style check of the fast memory tier (human code, not editable by the model).

    python3 -m kernel.memcheck --root <data root> [--n 3] [--top-k 10]

Five abilities, matched to what memfeed stores (decision 2026-11-06); questions and ground truth are computed by plain
code from the trajectory rows the feeder reads (reflection, harness_edit, version_result, buyer_exec):
  static    the lesson or the edit reason recorded for a given harness version (substring of a key token)
  dynamic   the latest seller harness version and its latest conversion (older version_result rows exist)
  workflow  a count inside a procedure (offers our buyer sent before a deal at a hashed shop)
  gotcha    what went wrong at a shop (the reason a buyer attempt did not end accepted)
  premise   a nonexistent harness version or shop id; the right answer is 모름/없음
Each question: search the daemon (top_k), then one model call that reads only the retrieved memories. Scoring is
simple: number within tolerance, string contained, or abstention. At most n questions per ability. Result goes to
<root>/a2/memcheck.json; due() says when the 6-hour cadence calls for the next run. The memory and model are injected
callables, so tests use fakes. Stdlib only.
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
_TOKEN = re.compile(r"[0-9A-Za-z가-힣_.]+")
KINDS = ("reflection", "harness_edit", "version_result", "buyer_exec")
EVERY_S = 21600


def key_token(text):
    """The longest word of text (at least 2 characters), the substring a correct answer must contain."""
    words = [w.strip(".") for w in _TOKEN.findall(str(text or ""))]
    words = [w for w in words if len(w) >= 2]
    return max(words, key=len) if words else None


def _recent(rows, kind):
    return sorted(
        (r for r in rows if r.get("kind") == kind), key=lambda r: -(r.get("ts") or 0)
    )


def build_questions(traj_rows, n=3):
    """Question dicts (ability, agent, question, query, check, truth), the n most recent per ability."""
    qs = {a: [] for a in ABILITIES}
    static = []
    for r in _recent(traj_rows, "reflection"):
        tok, t, v = key_token(r.get("lesson")), r.get("target"), r.get("version")
        if tok and t in ("seller", "buyer", "operator"):
            static.append(
                (
                    r.get("ts") or 0,
                    {
                        "agent": t,
                        "question": f"{t} 하네스 판 {v}의 회고에 적힌 교훈은 무엇인가?",
                        "query": f"운영자 회고 {t} 하네스 판 {v} 교훈",
                        "check": "contains",
                        "truth": tok,
                    },
                )
            )
    for r in _recent(traj_rows, "harness_edit"):
        tok, t, v = key_token(r.get("reason")), r.get("target"), r.get("version")
        if tok and t in ("seller", "buyer", "operator"):
            static.append(
                (
                    r.get("ts") or 0,
                    {
                        "agent": "operator",
                        "question": f"{t} 하네스를 판 {v}로 고친 이유는 무엇인가?",
                        "query": f"하네스 수정 {t} 판 {v} 이유",
                        "check": "contains",
                        "truth": tok,
                    },
                )
            )
    qs["static"] = [q for _, q in sorted(static, key=lambda x: -x[0])]
    results = [
        r
        for r in _recent(traj_rows, "version_result")
        if r.get("target") == "seller" and isinstance(r.get("version"), int)
    ]
    if len(results) >= 2:
        last = results[0]
        qs["dynamic"].append(
            {
                "agent": "seller",
                "question": "판매자 하네스는 여러 번 바뀌었다. 성과가 기록된 가장 최근 판의 번호는?",
                "query": "seller 하네스 판 성과",
                "check": "number",
                "truth": last["version"],
            }
        )
        conv = (last.get("metrics") or {}).get("conversion")
        if isinstance(conv, (int, float)) and not isinstance(conv, bool):
            qs["dynamic"].append(
                {
                    "agent": "seller",
                    "question": f"seller 하네스 판 {last['version']}의 가장 최근 성과 기록에서 성사율은 얼마인가? "
                    "0과 1 사이의 수로 답하라.",
                    "query": f"seller 하네스 판 {last['version']} 성과 성사율",
                    "check": "number",
                    "truth": conv,
                    "tol": 0.01,
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
    seen = json.dumps(traj_rows, ensure_ascii=False, default=str)
    versions = [
        r["version"]
        for r in traj_rows
        if isinstance(r.get("version"), int) and not isinstance(r["version"], bool)
    ]
    fake_v = max(versions, default=0) + 100
    i = 0
    while len(qs["premise"]) < n:
        if len(qs["premise"]) % 2 == 0:
            qs["premise"].append(
                {
                    "agent": "seller",
                    "question": f"seller 하네스 판 {fake_v}의 회고에 적힌 교훈은 무엇인가?",
                    "query": f"운영자 회고 seller 하네스 판 {fake_v} 교훈",
                    "check": "abstain",
                    "truth": "모름",
                }
            )
            fake_v += 1
            continue
        fake = "h" + hashlib.sha1(f"premise-{i}".encode()).hexdigest()[:6]
        i += 1
        if fake in seen:
            continue
        qs["premise"].append(
            {
                "agent": "buyer",
                "question": f"가게 {fake}와 거래가 성사될 때까지 우리 구매자가 그 가게에 보낸 가격 제안은 몇 번인가?",
                "query": f"구매 시도 가게 {fake}",
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
    t, v = float(q["truth"]), float(nums[0])
    tol = q.get("tol", max(REL_TOL * abs(t), 0.5))
    vals = (v, v / 100) if "%" in answer else (v,)
    return any(abs(x - t) <= tol for x in vals)


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
    path = os.path.join(root, "a2/trajectory.jsonl")
    return [
        r
        for k in KINDS
        for r in c.tail_json(path, 50_000_000, f'"{k}"'.encode())
        if r.get("kind") == k
    ]


def due(root, now, every_s=EVERY_S):
    """True when a2/memcheck.json is missing, has no ts, or is older than every_s."""
    doc = c.load_json(os.path.join(root, "a2/memcheck.json"))
    ts = doc.get("ts") if isinstance(doc, dict) else None
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return True
    return now - ts >= every_s


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
        "ts": 1_794_000_000,
        "agent": "operator",
        "kind": "harness_edit",
        "target": "seller",
        "version": 2,
        "files": ["systemprompt.md"],
        "reason": "첫 요구가가 높아 거절이 반복됨",
    },
    {
        "ts": 1_794_000_050,
        "agent": "operator",
        "kind": "version_result",
        "target": "seller",
        "version": 1,
        "since_ts": 1_793_998_000,
        "until_ts": 1_794_000_000,
        "metrics": {"requests": 150, "offers": 120, "sales": 12, "conversion": 0.08},
    },
    {
        "ts": 1_794_000_060,
        "agent": "operator",
        "kind": "reflection",
        "target": "seller",
        "version": 2,
        "effects": [
            {"edit": "첫 요구가 낮춤", "effect": "도움", "evidence": "판매 12건에서 19건"}
        ],
        "lesson": "구매자는 두번째 제안에서 수락하는 경우가 많음",
    },
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
    {
        "ts": 1_794_003_700,
        "agent": "operator",
        "kind": "version_result",
        "target": "seller",
        "version": 2,
        "since_ts": 1_794_000_000,
        "until_ts": 1_794_003_600,
        "metrics": {"requests": 160, "offers": 130, "sales": 19, "conversion": 0.12},
    },
]


def build_questions_for_test(n=3):
    """Questions from the fixed fixture rows above: at least one per ability."""
    return build_questions(FIXTURE_TRAJ, n)


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
    questions = build_questions(load_rows(root), n=a.n)
    res = run(
        questions,
        lambda agent, query, k: memory.search(sock, agent, query, k, timeout=5.0),
        lambda prompt: terra.complete(
            prompt, agent="operator", timeout_s=90, effort="medium"
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
