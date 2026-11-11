"""Feed outcomes into the mem0 daemon in batches, off the decision path (human code, not editable by the model).

    python3 -m kernel.memfeed --root <data root> [--once] [--interval-s 300]

Only what code cannot aggregate goes in (decision 2026-11-06). Reads, since the byte cursors in <root>/a2/memfeed.json,
a2/trajectory.jsonl rows of kind:
  reflection      the operator's effect table and lesson for a harness version: to the target agent and operator
  version_result  one factual sentence per row (version, window, numbers): to the target agent and operator
  harness_edit    what changed and why: to operator
  buyer_exec      attempt and outcome rows, shop habits seen from the buyer side: to buyer
Settle rows and raw seller/buyer decision rows are not fed: SKU prices and conversion are aggregated by kernel tools.
Each row becomes short Korean sentences built mechanically. Sentences go to the daemon as add(infer=True) in batches
of up to BATCH, per agent; mem0's infer step extracts the facts. Only kinds named in that agent's harness memory.json
"store" list are sent (operator: harness/operator/memory.json, else OPERATOR_STORE). The cursor moves only after every
batch was accepted, so a run is idempotent; on the first run rows older than --since-hours are skipped. Stdlib only.
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
    (
        "a2/trajectory.jsonl",
        (b'"buyer_exec"', b'"reflection"', b'"version_result"', b'"harness_edit"', b'"experiment_end"'),
    ),
)
AGENTS = ("seller", "buyer", "operator")
OPERATOR_STORE = ("harness_edit", "reflection", "version_result")
ALWAYS_STORE = ("experiment_end",)  # kernel verdicts: the only lessons the compact operator structure produces
EXP_STATUS = {"adopted": "채택", "dropped": "버림", "kept": "유지", "reverted": "되돌림", "voided": "무효"}
FIELD_CAP = 200
METRIC_LABELS = (
    ("requests", "요청"),
    ("offers", "제안"),
    ("declines", "거절"),
    ("sales", "판매"),
    ("conversion", "성사율"),
    ("orders", "주문"),
    ("deals", "거래"),
    ("deal_rate", "거래율"),
    ("price_to_list_median", "정가 대비 가격 중앙값"),
    ("stars_mean", "별점 평균"),
)


def store_kinds(harness_dir=HARNESS):
    """{agent: set of stored kinds} from each harness memory.json (operator defaults to OPERATOR_STORE)."""
    out = {}
    for agent in AGENTS:
        path = os.path.join(harness_dir, agent, "memory.json")
        if agent == "operator" and not os.path.exists(path):
            out[agent] = set(OPERATOR_STORE)
            continue
        cfg = c.load_json(path, {}) or {}
        st = cfg.get("store") if isinstance(cfg, dict) else None
        out[agent] = (
            {x for x in st if isinstance(x, str)} if isinstance(st, list) else set()
        )
    return out


def _short(v):
    return str(v)[:FIELD_CAP]


def _num(v):
    return round(v, 3) if isinstance(v, float) else v


def _reflection_text(row):
    out = [f"운영자 회고 {row.get('target')} 하네스 판 {row.get('version')}"]
    for e in row.get("effects") or []:
        if not isinstance(e, dict):
            continue
        part = f"수정 {_short(e.get('edit'))} 효과 {e.get('effect')}"
        if e.get("evidence"):
            part += f" 근거 {_short(e['evidence'])}"
        out.append(part)
    lesson = row.get("lesson")
    out.append(f"교훈 {_short(lesson)}" if lesson else "교훈 없음")
    return out


def _version_text(row):
    out = [f"{row.get('target')} 하네스 판 {row.get('version')} 성과"]
    a, b = row.get("since_ts"), row.get("until_ts")
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        out.append(f"기간 {c.kst(a)}~{c.kst(b)}")
    m = row.get("metrics") or {}
    for k, label in METRIC_LABELS:
        v = m.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(f"{label} {_num(v)}")
    return out


def _edit_text(row):
    files = row.get("files") or []
    return [
        f"하네스 수정 {row.get('target')} 판 {row.get('version')}",
        "파일 " + " ".join(map(str, files)) if files else "파일 없음",
        f"이유 {_short(row.get('reason'))}",
    ]


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


def _experiment_text(row):
    """One factual sentence for an ended experiment: what was tried, the verdict and its numbers."""
    v = row.get("verdict") if isinstance(row.get("verdict"), dict) else {}
    parts = [
        f"실험 #{row.get('id')} {_short(row.get('section'))} {row.get('key')} {row.get('old')} → {row.get('new')}"
        f" ({'동시 비교' if row.get('mode') == 'split' else '전후 비교'})",
        f"판정 {EXP_STATUS.get(row.get('status'), row.get('status'))}",
    ]
    for k, label in (("a", "기존 값 쪽 시간당"), ("b", "새 값 쪽 시간당"), ("before", "수정 전"), ("after", "수정 후"),
                     ("effect", "차이"), ("se", "표준오차"), ("n_a", "기존 쪽 판매 수"), ("n_b", "새 쪽 판매 수"),
                     ("hours", "비교 시간")):
        if _num(v.get(k)):
            parts.append(f"{label} {round(v[k], 3) if abs(v[k]) < 100 else round(v[k])}")
    return parts


def sentence(row, login=""):
    """[(agent, kind, sentence), ...] for a row worth storing, else None.

    Settle and decision rows return None (aggregated by kernel tools instead). buyer_exec rows already carry hashed
    shop ids from buyer_exec, so they are not hashed again. The sentence is masked last.
    """
    if not isinstance(row, dict):
        return None
    kind = row.get("kind")
    target = row.get("target")
    if kind == "buyer_exec":
        agents, parts = ("buyer",), _buyer_exec_text(row)
    elif kind in ("reflection", "version_result") and target in AGENTS:
        agents = tuple(dict.fromkeys((target, "operator")))
        parts = (_reflection_text if kind == "reflection" else _version_text)(row)
    elif kind == "harness_edit" and target in AGENTS:
        agents, parts = ("operator",), _edit_text(row)
    elif kind == "experiment_end" and row.get("status") in EXP_STATUS:
        agents, parts = ("operator",), _experiment_text(row)
    else:
        return None
    if not parts:
        return None
    ts = row.get("ts")
    when = c.kst(ts) + " " if isinstance(ts, (int, float)) else ""
    text = c.mask(when + ", ".join(parts) + ".", (login,))
    return [(a, kind, text) for a in agents]


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
        out = [s for s in sentence(row, login) or () if s[1] in ALWAYS_STORE or s[1] in kinds.get(s[0], set())]
        return out or None

    for src, needles in SOURCES:
        found, new_cur[src] = read_new(
            os.path.join(root, src),
            int(cur.get(src) or 0),
            keep,
            MAX_PER_RUN,
            now - since_hours * 3600 if first else None,
            needles,
        )
        for item in found:
            for agent, _, text in item:
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
