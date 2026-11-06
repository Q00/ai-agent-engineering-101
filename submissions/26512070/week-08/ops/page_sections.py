"""The status page's new tabs (user 11/6 13:xx: everything I monitor on one page, easy to read - the strategy was not
visible): home, strategy, meta-harness, market and knowledge. Each section reads local records only and is built on
its own (ops/status_page.py catches a broken one). Korean labels for the user; numbers as they are recorded.
"""
import html
import json
import time
from pathlib import Path

from core import atomic, paths

PART = {"book": "장부(book)", "revenue": "매출", "stars_rank": "별점", "saved": "아낀 비율(saved)",
        "forum": "게시판", "deals": "거래 수", "posts": "글 수"}
METRIC = {"agreed_unpaid_rate": "합의 후 미결제율", "revenue": "매출", "saved_share": "아낀 비율",
          "margin_rate": "마진율", "sales": "판매 수"}


def e(x):
    return html.escape(str(x if x is not None else "-"))


def won(x):
    return "-" if not isinstance(x, (int, float)) else f"{round(x):,}"


def hm(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "-"


def jlines(p):
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def table(head, rows, cls=""):
    h = "".join(f"<th>{e(c)}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class='tw {cls}'><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>"


def bar(p, label=""):
    p = max(0.0, min(1.0, p or 0))
    return f"<div class=bar><i style='width:{p * 100:.0f}%'></i><span>{e(label)}</span></div>"


def card(title, body, tone=""):
    return f"<section class='card {tone}'><h3>{e(title)}</h3>{body}</section>"


def kpi(items):
    return "<div class=kpi>" + "".join(f"<div><b>{v}</b><small>{e(k)}</small></div>" for k, v in items) + "</div>"


def H():
    return paths.memory() / "harness"


# --- home

def sec_home(d):
    from harness import alert, experiment, ops, queue, wisdom
    c = atomic.read_json(H() / "score_class.json", default=None) or {}
    parts = []
    for side, label in (("shop", "가게"), ("buyer", "buyer")):
        for f, x in ((c.get(side) or {}).get("parts") or {}).items():
            room = x["weight"] * (1 - x["percentile"])
            parts.append(f"<div class=part><div class=pl><b>{e(label)} · {e(PART.get(f, f))}</b>"
                         f"<small>무게 {x['weight']} · 남은 여지 {room:.3f} · 위: {e(x.get('next'))}</small></div>"
                         + bar(x["percentile"], f"{x['percentile']:.2f}") + "</div>")
    head = kpi([("Class view 순위", f"{e(c.get('rank'))}<small>/{e(c.get('students'))}</small>"),
                ("백분위", e(c.get("percentile"))), ("학생 합계", e(c.get("total"))),
                (f"위 {c.get('next') or '-'}까지", e(c.get("gap_next"))), ("3위권까지", e(c.get("gap_top3")))])
    pend = wisdom.status()
    pend_html = "".join(
        f"<div class=req><b>{e(p['title'])}</b><pre>{e(wisdom.render(p))}</pre>"
        f"<code>py -m harness.wisdom decide {e(p['id'])} approve</code></div>" for p in pend) or "<p class=mute>없음</p>"
    exps = []
    for x in experiment.ledger()["experiments"].values():
        if x["status"] != "running" and d.now - x.get("end", 0) > 6 * 3600:
            continue
        r = experiment.result(x["id"], d.now)
        if x["method"] == "ab":
            val = (f"A {e((r.get('A') or {}).get('value'))} (n {e((r.get('A') or {}).get('n'))}) · "
                   f"B {e((r.get('B') or {}).get('value'))} (n {e((r.get('B') or {}).get('n'))})")
        else:
            val = f"전 {e(r.get('before'))} → 후 {e(r.get('after'))}"
        exps.append(f"<li><b>{e(x['why'][:90])}</b><small>{e(METRIC.get(x['metric'], x['metric']))}: {val} · "
                    f"판정(코드) <b>{e(r.get('verdict'))}</b> · {e(x['status'])} · 끝 {e(hm(x.get('end')))}</small></li>")
    q = queue.entries()
    emerg = ops.emergency()
    al = [a for a in alert.recent(24, d.now) if a["level"] in ("now", "warn")][-10:][::-1]
    return (card("지금 위치 (학생끼리, 순위표 Class view)", head + "".join(parts))
            + card(f"승인 대기 {len(pend)}건", pend_html, "warn" if pend else "")
            + card("실험", "<ul class=kn>" + "".join(exps) + "</ul>" if exps else "<p class=mute>없음</p>")
            + (card("예약 (차례가 오면 설치)", "<ul>" + "".join(
                f"<li>{e(x['request'])} · {e(x.get('after_experiment') or hm(x.get('after')))} 뒤</li>" for x in q)
                + "</ul>") if q else "")
            + (card("비상 동작 중", "<ul>" + "".join(f"<li><b>{e(k)}</b>: {e(v.get('why'))}</li>" for k, v in emerg.items())
                    + "</ul>", "bad") if emerg else "")
            + card("최근 알림 (24시간)", "<ul class=alerts>" + "".join(
                f"<li class={e(a['level'])}><time>{e(hm(a['ts'])[6:])}</time> {e(a['text'][:180])}</li>" for a in al)
                + "</ul>" if al else "<p class=mute>없음</p>"))


# --- strategy

def sec_strategy(d):
    from harness import capability
    s = d.strategy
    cap = capability.capability_list()["numbers"]
    roles = []
    for role, label in (("seller", "seller"), ("buyer", "buyer"), ("restock", "restock")):
        rows = []
        for k, v in sorted((cap.get(role) or {}).get("rows", {}).items()):
            if not v["read_by_code"]:
                continue
            sch = v["schema"]
            rng = f"{sch.get('minimum', '')}~{sch.get('maximum', '')}" if "minimum" in sch or "maximum" in sch else ""
            rows.append([f"<code>{e(k)}</code>", f"<b>{e(v['current'])}</b>", e(rng)])
        roles.append(card(f"{label} 숫자 (플레이북)", table(["숫자", "지금", "허용 범위"], rows, "narrow")))
    prio = "".join(f"<li>{e(p)}</li>" for p in s.get("priorities") or [])
    limits = s.get("limits") or {}
    lim = table(["한도", "값"], [[e(k), e(json.dumps(v, ensure_ascii=False))] for k, v in limits.items()], "narrow")
    rs = s.get("restock") or {}
    dec = jlines(H() / "decisions.jsonl")[-14:][::-1]
    drow = [[e(hm(x.get("ts"))), e(x.get("event") or x.get("choice")), e(x.get("id")),
             e((x.get("title") or x.get("note") or "")[:80]) + (f" → v{e(x['installed'])}" if x.get("installed") else "")
             + (f" → 실험 {e(x['experiment'])}" if x.get("experiment") else "")] for x in dec]
    hist = []
    for v in range((s.get("version") or 0), max(0, (s.get("version") or 0) - 8), -1):
        h = atomic.read_json(paths.memory() / "strategy" / "history" / f"v{v}.json", default=None)
        if h:
            hist.append([f"v{v}", e((h.get("approved") or "")[:110])])
    return (card(f"전략 v{s.get('version')}", f"<p class=mute>{e(s.get('approved'))}</p>"
                 f"<p>restock 정지: <b>{e(hm(rs.get('stop_after')))}</b> · 맥락: {e(' '.join(s.get('context') or [])[:300])}</p>")
            + card("우선순위 (모델이 예외를 판단할 때 읽음)", f"<ol class=prio>{prio}</ol>")
            + "<div class=grid>" + "".join(roles) + "</div>"
            + card("한도 (도구가 강제)", lim)
            + card("결정 기록 (승인 경로)", table(["시각", "무엇", "id", "내용"], drow))
            + card("전략 버전", table(["판", "승인"], hist)))


# --- meta-harness

def sec_meta(d):
    from harness import meta
    files = sorted((H() / "meta").glob("*.json"))
    last = atomic.read_json(files[-1], default=None) if files else None
    qs = meta.questions()
    rows = []
    for f in files[-10:][::-1]:
        r = atomic.read_json(f, default=None) or {}
        p = (r.get("steps") or {}).get("proposal") or {}
        rows.append([e(f.stem[-4:-2] + ":" + f.stem[-2:]), e("✓" if p.get("ok") else "✗"), e(p.get("steps")),
                     e(len(r.get("requests") or [])), e(((r.get("report") or p.get("last") or "")[:90]))])
    body = ""
    if last:
        p = last["steps"].get("proposal") or {}
        body = (f"<p class=mute>{e(hm(last['ts']))} · 맥락 {e(last.get('context_chars'))}자 · 조회 {e(p.get('steps'))}단계</p>"
                + f"<pre class=report>{e(last.get('report') or p.get('last') or '보고 없음')}</pre>"
                + "<details><summary>조회한 도구</summary><ul class=mono>"
                + "".join(f"<li>{e(x)}</li>" for x in p.get("looked_up") or []) + "</ul></details>"
                + "<details><summary>분석(sonnet)</summary><pre>"
                + e((last["steps"].get("analysis") or {}).get("text") or "") + "</pre></details>")
    return (card("사람이 낸 질문 (매 주기 먼저 답함)", "<ol>" + "".join(f"<li>{e(q['q'])}</li>" for q in qs) + "</ol>"
                 if qs else "<p class=mute>없음 · <code>py -m harness.meta ask \"...\"</code></p>")
            + card("최근 메타 주기 보고 (30분마다)", body or "<p class=mute>아직 없음</p>")
            + card("지난 주기", table(["시각", "제안", "단계", "요청", "보고 첫 줄"], rows)))


# --- market

def sec_market2(d):
    from harness import knowledge, market
    b = market.benchmark(hours=6, top=6, now=d.now)
    rows = []
    for r in b["top"] + ([b["ours"]] if b.get("ours") and b["ours"]["rank"] > 6 else []):
        name = "<b>우리</b>" if r["shop"] == "ours" else e(r["shop"][:22])
        rows.append([e(r["rank"]), name, bar(min(1, (r["share_of_market"] or 0) * 4), f"{(r['share_of_market'] or 0):.1%}"),
                     e(r["deals_per_h"]), e(r["median_share_of_list"]), won(r["mean_list_price"])])
    watch = []
    for it in sorted(knowledge.get(prefix="market:price:highlist", now=d.now), key=lambda i: i["made"])[-12:]:
        sh = next((x for x in knowledge.get(prefix="shop:share_of_market", now=d.now)
                   if abs(x["made"] - it["made"]) < 600), None)
        watch.append([e(hm(it["made"])), e((it.get("data") or {}).get("median_share")),
                      e(((sh or {}).get("data") or {}).get("share_of_market")),
                      e(((sh or {}).get("data") or {}).get("rank"))])
    return (card(f"시장 점유율 상위 가게 (최근 6시간, 시장 {won(b['market_revenue'])}원)",
                 table(["순위", "가게", "점유율", "시간당 거래", "정가 대비", "판 물건 평균 정가"], rows))
            + card("시간별: 고가 goods 거래가와 우리 점유율 (매시간 기록)",
                   table(["시각", "고가 goods 정가 대비", "우리 점유율", "우리 순위"], watch[::-1])
                   if watch else "<p class=mute>매시간 쌓임</p>"))


# --- knowledge

def sec_knowledge(d):
    from harness import knowledge
    items = knowledge.get(now=d.now)
    hyp = [i for i in items if i["grade"] == "hypothesis"]
    obs = [i for i in items if i["grade"] == "observed" and not i["about"].startswith(("participant:", "market:price",
                                                                                         "shop:share"))][:12]
    stated = [i for i in items if i["grade"] == "stated"][:8]
    li = lambda xs: "<ul class=kn>" + "".join(                       # noqa: E731
        f"<li><b>{e(i['about'])}</b> {e(i['claim'][:300])} <small>{e(i['source'])} · {e(hm(i['made']))}"
        f"{' · 재검토 필요' if i.get('review') else ''}</small></li>" for i in xs) + "</ul>"
    n_part = len([i for i in items if i["about"].startswith("participant:")])
    return (card(f"가설 {len(hyp)}건 (실험으로 검증할 것)", li(hyp) if hyp else "<p class=mute>없음</p>")
            + card("관측 (데이터에서 다시 계산 가능)", li(obs) if obs else "<p class=mute>없음</p>")
            + card("명시 (시장 원문, 사람의 결정)", li(stated) if stated else "<p class=mute>없음</p>")
            + f"<p class=mute>참가자 지식 {n_part}건 (GitHub 계정별, 24시간)</p>")


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c1e21;--mut:#6b7280;--line:#e5e7eb;--acc:#4f46e5;--acc2:#c7d2fe;--warn:#b45309;
--warnbg:#fffbeb;--bad:#b91c1c;--badbg:#fef2f2;--ok:#047857}
@media (prefers-color-scheme:dark){:root{--bg:#111214;--card:#1a1b1e;--fg:#e8e8ea;--mut:#9aa0a6;--line:#2a2c30;
--acc:#8b93f8;--acc2:#2e3170;--warn:#f59e0b;--warnbg:#2a2210;--bad:#f87171;--badbg:#2a1414;--ok:#34d399}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 system-ui,-apple-system,"Malgun Gothic","Apple SD Gothic Neo",sans-serif}
header{padding:14px 16px 6px}header h1{font-size:18px;margin:0}header p{margin:2px 0 0;color:var(--mut);font-size:12px}
.tabs{display:flex;flex-wrap:wrap;padding:0 8px}.tabs>input{display:none}
.tabs>label{padding:8px 12px;margin:2px;cursor:pointer;color:var(--mut);border-radius:999px;font-weight:600;font-size:13px}
.tabs>.panel{display:none;order:99;flex:0 0 100%;width:100%;max-width:100%;min-width:0;padding:8px 8px 48px}
body{overflow-x:hidden}.card{min-width:0;overflow:hidden}.tw{max-width:100%}
.tabs>input:checked+label{color:#fff;background:var(--acc)}.tabs>input:checked+label+.panel{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:10px 0}
.card.warn{border-color:var(--warn);background:var(--warnbg)}.card.bad{border-color:var(--bad);background:var(--badbg)}
.card h3{font-size:14px;margin:0 0 8px}.mute{color:var(--mut);font-size:12px}
.kpi{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px;margin:4px 0 10px}
.kpi>div{background:var(--bg);border-radius:10px;padding:8px 10px}.kpi b{font-size:20px;display:block}
.kpi b small{font-size:12px;color:var(--mut)}.kpi>div>small{color:var(--mut);font-size:11px}
.part{margin:8px 0}.pl{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}.pl small{color:var(--mut);font-size:11px}
.bar{position:relative;height:18px;background:var(--bg);border-radius:9px;overflow:hidden;margin-top:3px;min-width:80px}
.bar i{position:absolute;inset:0 auto 0 0;background:var(--acc2)}
.bar span{position:relative;font-size:11px;padding-left:6px;line-height:18px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:0 10px}
.tw{overflow-x:auto}table{border-collapse:collapse;font-size:12px;width:100%}
th,td{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}
th{color:var(--mut);font-weight:600;white-space:nowrap}td{white-space:nowrap}.narrow td{white-space:normal}
pre{white-space:pre-wrap;word-break:break-word;font-size:12px;background:var(--bg);padding:8px;border-radius:8px;margin:6px 0}
pre.report{font-size:13px;line-height:1.55}code{font-size:11px;background:var(--bg);padding:1px 5px;border-radius:5px}
.req{border-top:1px dashed var(--line);padding-top:8px;margin-top:8px}
ul,ol{padding-left:20px;margin:4px 0}li{margin:3px 0}.prio li{margin:6px 0}
.alerts li{list-style:none;margin-left:-20px}.alerts time{color:var(--mut);font-size:11px;margin-right:4px}
.alerts .now{color:var(--bad)}.alerts .warn{color:var(--warn)}
.kn li small{display:block;color:var(--mut);font-size:11px}.mono{font-family:ui-monospace,Consolas,monospace;font-size:11px}
details summary{cursor:pointer;color:var(--acc);font-size:12px;margin-top:6px}h3{font-size:14px}
"""
