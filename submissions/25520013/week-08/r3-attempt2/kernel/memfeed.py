"""Feed outcomes into the mem0 daemon in batches, off the decision path (human code, not editable by the model).

    python3 -m kernel.memfeed --root <data root> [--once] [--interval-s 300]

Reads, since the byte cursors in <root>/a2/memfeed.json:
  a2/trajectory.jsonl  kind "buyer_exec" (attempt and outcome) and kind "decision"
  .r3/events.jsonl     settle rows we took part in: our shop's sales to seller memory, our buyer's purchases to buyer memory
Each row becomes one short factual Korean sentence built mechanically (time, what happened, SKU, hashed shop,
prices, result; no judgment, no lessons). Sentences go to the daemon as add(infer=True) in batches of up to
BATCH, for the agent the fact belongs to; mem0's infer step extracts the facts. Only kinds named in that agent's
harness memory.json "store" list are sent. The cursor moves only after every batch was accepted, so a run is
idempotent; on the first run rows older than --since-hours are skipped. Stdlib only.
"""

import argparse
import json
import os
import time

from kernel import common as c
from kernel import memory

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(HERE, "harness")
BATCH = 20
MAX_PER_RUN = 200
SOURCES = (
    ("a2/trajectory.jsonl", (b'"buyer_exec"', b'"decision"')),
    (".r3/events.jsonl", (b'"settle"',)),
)


def store_kinds(harness_dir=HARNESS):
    """{agent: set of stored kinds} from each harness memory.json."""
    out = {}
    for agent in ("seller", "buyer"):
        cfg = c.load_json(os.path.join(harness_dir, agent, "memory.json"), {}) or {}
        st = cfg.get("store") if isinstance(cfg, dict) else None
        out[agent] = (
            {x for x in st if isinstance(x, str)} if isinstance(st, list) else set()
        )
    return out


def _err_kind(e):
    """'model: ModelRefused: ...' -> 'model ModelRefused'; a rule error -> 'model rule'."""
    parts = str(e).split(": ")
    if len(parts) >= 3 and parts[1][:1].isupper() and parts[1].isidentifier():
        return f"{parts[0]} {parts[1]}"
    return f"{parts[0]} rule"


def _decision_text(row):
    d = row.get("decision") or {}
    kind = row.get("decision_kind")
    out = [f"{row.get('agent')} 결정 {kind}", f"경로 {row.get('route_used')}"]
    if kind == "buyer.plan":
        cands = d.get("candidates") or []
        out.append(f"후보 {len(cands)}개")
        for x in cands[:3]:
            if isinstance(x, dict):
                out.append(
                    f"SKU {x.get('sku')} 최대 지불 {x.get('max_pay')} 제안 {x.get('offers')}"
                )
    else:
        out.append(f"행동 {d.get('action')}")
        for k, label in (("unit", "유닛"), ("ask", "요구가"), ("price", "가격")):
            if d.get(k) is not None:
                out.append(f"{label} {d[k]}")
    if row.get("memory_keys"):
        out.append("관련 키 " + " ".join(map(str, row["memory_keys"])))
    errs = row.get("errors") or []
    if errs:
        out.append("오류 " + ", ".join(dict.fromkeys(_err_kind(e) for e in errs)))
    return out


def _buyer_exec_text(row):
    if row.get("event") == "attempt":
        out = [
            f"구매 시도 주문 {row.get('order')}",
            f"가게 {row.get('shop')}",
            f"SKU {row.get('sku')}",
            f"최대 지불 {row.get('max_pay')}",
            f"보낸 제안 {row.get('offers_sent')} ({len(row.get('offers_sent') or [])}번)",
            f"가게 마지막 요구가 {row.get('final_ask')}",
            f"결과 {row.get('result')}",
        ]
        if row.get("why"):
            out.append(f"사유 {row['why']}")
        return out
    if row.get("event") == "outcome":
        if row.get("deal"):
            out = [
                f"구매 결과 주문 {row.get('order')}",
                "거래 성사",
                f"가게 {row.get('shop')}",
                f"가격 {row.get('price')}",
            ]
        else:
            out = [f"구매 결과 주문 {row.get('order')}", "거래 없음"]
        out.append(f"후보 {row.get('candidates')}개 중 {row.get('tried')}개 시도")
        if row.get("stop"):
            out.append(f"중단 {row['stop']}")
        return out
    return None


def _settle_text(data, side="shop"):
    out = [
        "우리 가게 판매 정산" if side == "shop" else "우리 구매자 구매 정산",
        f"SKU {data.get('sku')}",
        f"금액 {data.get('amount')}",
    ]
    other = ("buyer", "구매자") if side == "shop" else ("shop", "가게")
    for k, label in (other, ("unit", "유닛"), ("grade", "등급")):
        if data.get(k) not in (None, ""):
            out.append(f"{label} {data[k]}")
    return out


def sentence(row, login=""):
    """(agent, kind, sentence) for a row worth storing, else None.

    Settle data is scrubbed (other players hashed); buyer_exec rows already carry hashed shop ids from buyer_exec,
    so they are not hashed again. The sentence is masked last.
    """
    if not isinstance(row, dict):
        return None
    ts = row.get("ts")
    if row.get("type") == "settle":
        data = row.get("data") or {}
        side = c.settle_side(data, login)
        if side is None:
            return None
        agent = "seller" if side == "shop" else "buyer"
        agent, kind, parts = agent, "settle", _settle_text(c.scrub(data, login), side)
    elif row.get("kind") == "buyer_exec":
        agent, kind, parts = (
            "buyer",
            "buyer_exec",
            _buyer_exec_text(row),
        )
    elif row.get("kind") == "decision" and row.get("agent") in ("seller", "buyer"):
        agent, kind, parts = row["agent"], "decision", _decision_text(row)
    else:
        return None
    if not parts:
        return None
    when = c.kst(ts) + " " if isinstance(ts, (int, float)) else ""
    return agent, kind, c.mask(when + ", ".join(parts) + ".", (login,))


def read_new(path, offset, keep, limit, since_ts=None, needles=None):
    """Complete JSON lines from byte offset -> (kept, new_offset).

    keep(row) returns the value to collect or None. Stops after limit kept values; a shrunk file restarts at 0.
    needles (bytes) pre-filter lines before parsing.
    """
    out = []
    try:
        fh = open(path, "rb")
    except OSError:
        return out, offset
    with fh:
        fh.seek(0, 2)
        if fh.tell() < offset:
            offset = 0
        fh.seek(offset)
        pos = offset
        for line in fh:
            if not line.endswith(b"\n") or len(out) >= limit:
                break
            pos += len(line)
            if needles and not any(n in line for n in needles):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or (
                since_ts is not None and (row.get("ts") or 0) < since_ts
            ):
                continue
            v = keep(row)
            if v is not None:
                out.append(v)
    return out, pos


def run_once(
    root, sock, harness_dir=HARNESS, since_hours=24.0, now=None, add=memory.add
):
    """One pass. Returns {"sent": n sentences, "batches": n, "ok": bool}."""
    root = c.data_root(root)
    now = c.now_ts() if now is None else now
    login = c.owner_login(root)
    cur_path = os.path.join(root, "a2/memfeed.json")
    cur = c.load_json(cur_path)
    first = not isinstance(cur, dict)
    cur = {} if first else cur
    kinds = store_kinds(harness_dir)
    by_agent, new_cur = {}, dict(cur)
    def keep(row):
        s = sentence(row, login)
        return s if s and s[1] in kinds.get(s[0], set()) else None

    for src, needles in SOURCES:
        found, new_cur[src] = read_new(
            os.path.join(root, src),
            int(cur.get(src) or 0),
            keep,
            MAX_PER_RUN,
            now - since_hours * 3600 if first else None,
            needles,
        )
        for agent, _, text in found:
            by_agent.setdefault(agent, []).append(text)
    sent = batches = 0
    for agent, lines in by_agent.items():
        for i in range(0, len(lines), BATCH):
            chunk = lines[i : i + BATCH]
            meta = {"source": "memfeed", "n": len(chunk)}
            if not add(sock, agent, "\n".join(chunk), infer=True, metadata=meta):
                return {"sent": sent, "batches": batches, "ok": False}
            sent, batches = sent + len(chunk), batches + 1
    new_cur["ts"] = round(now, 3)
    os.makedirs(os.path.dirname(cur_path), exist_ok=True)
    tmp = cur_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new_cur, f)
    os.replace(tmp, cur_path)
    return {"sent": sent, "batches": batches, "ok": True}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None)
    ap.add_argument("--sock", default=None)
    ap.add_argument("--harness", default=HARNESS)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval-s", type=float, default=300)
    ap.add_argument("--since-hours", type=float, default=24.0)
    a = ap.parse_args(argv)
    sock = a.sock or memory.sock_path(a.root)
    while True:
        r = run_once(a.root, sock, a.harness, a.since_hours)
        print(
            json.dumps(dict(r, ts=round(time.time())), ensure_ascii=False), flush=True
        )
        if a.once:
            return 0 if r["ok"] else 1
        time.sleep(a.interval_s)


if __name__ == "__main__":
    raise SystemExit(main())
