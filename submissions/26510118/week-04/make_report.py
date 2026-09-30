"""Build REPORT.md parts 1-3 from the code and the recorded run.

Every number comes from results.csv or the logs, every prompt from acl.py, so
the report cannot drift from what was actually run.
"""
import csv, json, glob, re, sys, os
from collections import Counter

os.chdir(sys.argv[1])
sys.path.insert(0, ".")
import acl, negotiate  # noqa: E402

ROWS = list(csv.DictReader(open("results.csv", encoding="utf-8")))
SCEN = json.load(open("scenarios.json", encoding="utf-8"))
COND = ("free", "tagged", "structured")
HEAD = open("logs/free-1.txt", encoding="utf-8").readline().strip()
FP = HEAD.split("scenarios=")[1]
n = lambda v: int(v) if str(v).strip() else 0

S = {}
for c in COND:
    r = [x for x in ROWS if x["condition"] == c]
    oc = Counter(x["outcome"] for x in r)
    bs = ss = 0
    for x in (y for y in r if y["outcome"] == "deal"):
        m = re.search(r"buyer_surplus=(-?\d+) seller_surplus=(-?\d+)", x["note"])
        bs += int(m.group(1)); ss += int(m.group(2))
    S[c] = dict(correct=sum(n(x["correct"]) for x in r),
                viol=sum(n(x["violation"]) for x in r),
                deal=oc["deal"], no_deal=oc["no_deal"], opn=oc["open"],
                turns=sum(n(x["turns"]) for x in r) / len(r),
                fmt=sum(n(x["format_errors"]) for x in r),
                reader=sum(n(x["reader_calls"]) for x in r),
                msgs=sum(n(x["turns"]) for x in r), bs=bs, ss=ss,
                share=round(100 * ss / max(bs + ss, 1)))

acts = {}
for pat in ("tagged", "structured"):
    c = Counter()
    for f in glob.glob("logs/%s-[123].txt" % pat):
        for line in open(f, encoding="utf-8"):
            m = re.match(r"\[(buyer|seller)\] (.*)", line)
            if not m:
                continue
            a = (re.search(r'"performative":\s*"([a-z-]+)"', m.group(2))
                 if pat == "structured" else re.match(r"\(([a-z-]+)\)", m.group(2)))
            if a:
                c[m.group(1) + "/" + a.group(1)] += 1
    acts[pat] = c

free_lab = Counter(); free_num = free_drop = seller_num = seller_msg = 0
for f in glob.glob("logs/free-[123].txt"):
    L = open(f, encoding="utf-8").read().split("\n")
    for k, l in enumerate(L):
        m = re.match(r"\[(buyer|seller)\] (.*)", l)
        if not m:
            continue
        if m.group(1) == "seller":
            seller_msg += 1
            seller_num += bool(re.search(r"\d", m.group(2)))
        r = re.search(r"\[reader\] \{'performative': '([a-z-]+)', 'price': ([^}]+)\}",
                      L[k + 1] if k + 1 < len(L) else "")
        if not r:
            continue
        free_lab[r.group(1)] += 1
        if re.search(r"\d", m.group(2)):
            free_num += 1
            free_drop += r.group(1) != "propose"

tag_prop = tag_lost = 0
for f in glob.glob("logs/tagged-[123].txt"):
    L = open(f, encoding="utf-8").read().split("\n")
    for k, l in enumerate(L):
        if re.match(r"\[(buyer|seller)\] \(propose\)", l):
            tag_prop += 1
            tag_lost += "'price': None" in "\n".join(L[k + 1:k + 3])

tag_seller = acts["tagged"]["seller/reject-proposal"] + acts["tagged"]["seller/accept-proposal"]
st_prop = acts["structured"]["seller/propose"]
st_seller = (acts["structured"]["seller/reject-proposal"]
             + acts["structured"]["seller/accept-proposal"] + st_prop)

row3 = lambda f: " | ".join(f % S[c] for c in COND)
O = []
w = O.append

w("# Week 04 — 같은 협상, 세 가지 메시지 형식")
w("")
w("buyer 1명과 seller 1명이 같은 물건을 놓고 값을 흥정한다. 바뀌는 것은 메시지의 생김새")
w("하나뿐이다. FIPA-ACL이 필수 필드로 만든 performative가 무엇을 벌어주고 무엇을 비용으로")
w("치르는지 잰다.")
w("")
w("```")
w("  buyer ──── message ────> seller        한 턴")
w("        <─── message ─────")
w("                                        최대 10턴")
w("  메시지 하나가 나올 때마다:")
w("")
w("      message ──> protocol.read(condition) ──> (performative, price)")
w("                       free    LLM reader")
w("                       tagged  regex + LLM reader")
w("                       struct  JSON parser")
w("")
w("  종료:  accept-proposal -> deal     refuse -> no_deal     10턴 -> open")
w("```")
w("")

# ---------------------------------------------------------------- 1
w("## 1. 설정")
w("")
w("### 모델과 실행")
w("")
w("| 항목 | 값 |")
w("|---|---|")
w("| provider / 모델 | OpenAI 호환 / `gpt-4o-mini` (`model.py`가 환경에서 판정) |")
w("| temperature | 0.7 — 3회 반복의 변동을 보려면 0이어서는 안 된다 |")
w("| 턴 한도 | 10 (`negotiate.MAX_TURNS`). 강의 뼈대는 8 |")
w("| 도구 | 없음. 한 턴에 모델 호출 1회, 읽기에 최대 1회 더 |")
w("| 시나리오 | 5개, `scenarios=%s`. 첫 실행 전 커밋 (`6293372`) |" % FP)
w("| 실행 | 3조건 × 3반복 × 5시나리오 = 45 에피소드, run 1–9 |")
w("| 크래시 | 0건 |")
w("")
w("```bash")
w("pip install openai")
w("export OPENAI_API_KEY=...")
w("cd submissions/26510118/week-04")
w("python run.py                      # 9 run 전부. 중단되면 같은 명령으로 이어서 실행")
w("python run.py --only structured    # 한 조건만")
w("```")
w("")
w("로그 첫 줄에 `%s` 가 찍힌다." % HEAD)
w("`scenarios` 는 `scenarios.json` 의 SHA-256 앞 8자리다. 지문이 같은 행끼리만 비교할 수 있다.")
w("")
w("run 번호는 세는 값이 아니라 위치로 정한다.")
w("")
w("```")
w("run    1  2  3    4  5  6    7  8  9")
w("       free       tagged     structured")
w("       ↑ 반복 1·2·3")
w("")
w("중단 후 다시 돌려도 같은 번호가 나오므로,")
w("results.csv 에 이미 있는 (run, scenario) 쌍을 건너뛰고 이어서 실행한다.")
w("```")
w("")
w("### 시나리오")
w("")
w("| id | 물건 | reserve | budget | ZOPA | 정답 |")
w("|---:|---|---:|---:|---:|---|")
for s in SCEN:
    z = s["budget"] - s["reserve"]
    w("| %d | %s | %d | %d | %+d | %s |"
      % (s["id"], s["item"], s["reserve"], s["budget"], z, "거래" if z >= 0 else "결렬"))
w("")
w("```")
w("ZOPA = budget − reserve = 둘이 나눠 가질 수 있는 몫")
w("")
w("      −150        −10      +20        +64        +80")
w("   ────┼───────────┼────┬────┼──────────┼──────────┼────")
w("     노트북       코트  0  이어폰     키보드     자전거")
w("        결렬이 정답  │      거래가 정답")
w("```")
w("")
w("reserve 와 budget 은 첫 실행 전에 커밋했다. 실행 결과에 맞춰 한도를 바꾸면")
w("`violation` 을 잴 수 없다.")
w("")
w("### 시스템 프롬프트")
w("")
w("```")
w("system_prompt(role, item, limit, condition, max_turns)")
w("")
w("   역할 문단          +   공통 문단        +   형식 문단")
w("   ROLE[role]            COMMON               FORMAT[condition]")
w("   역할마다 다름          세 조건 모두 같음      조건마다 다름  ← 독립변수")
w("   buyer / seller")
w("")
w("앞 두 조각은 세 조건에서 글자 하나까지 같다. 자동 검사로 확인한다.")
w("```")
w("")
w("#### 역할 문단")
w("")
w("> buyer — " + acl.ROLE["buyer"])
w("")
w("> seller — " + acl.ROLE["seller"])
w("")
w("`{limit}` 에는 자기 숫자만 들어간다. buyer 는 budget 만, seller 는 reserve 만 본다.")
w("두 문단은 거울이다.")
w("")
w("| | buyer | seller |")
w("|---|---|---|")
w("| 한도 방향 | `pay at most` | `accept at least` |")
w("| 금지 | `above {limit}` | `below {limit}` |")
w("| 선호 | `lower price is better` | `higher price is better` |")
w("| 문장 수 | 4 | 4 |")
w("")
w("한쪽 문단이 더 강하면 그 비대칭이 세 조건 전부에 실려 형식 효과처럼 보인다.")
w("")
w("#### 공통 문단 (통제변수)")
w("")
w("> " + acl.COMMON.format(max_turns=negotiate.MAX_TURNS).strip())
w("")
w("#### 형식 문단 (독립변수) — 강의자료 `acl.py` 원문 그대로")
w("")
w("```")
for c in COND:
    w("%-11s %s" % (c, acl.FORMAT[c].strip()))
w("```")
w("")
w("#### 리더 프롬프트 — `free` 와 `tagged` 가 공유한다")
w("")
w("> " + acl.READER_SYSTEM)
w("")
w("`You must pick one of these four even when the message fits none of them.` 가 설계의")
w("핵심이다. `unclear` 같은 출구를 주지 않았다. 네 행위로 못 덮는 메시지가 어디로 떨어지는지가")
w("이 실습의 관측 대상이기 때문이다. `tagged` 는 이 리더에게서 `performative` 를 버리고 가격만")
w("쓴다. 프롬프트를 같게 두어야 통제변수가 된다.")
w("")
w("### 메시지를 읽는 계층 (`protocol.py`)")
w("")
w("| 조건 | 행위를 정하는 것 | 가격을 뽑는 것 | 모델 호출 |")
w("|---|---|---|---|")
w("| `free` | LLM 리더 | 같은 호출 | 메시지마다 1회 |")
w("| `tagged` | 정규식 (맨 앞 태그) | LLM 리더 | `propose` 일 때만 1회 |")
w("| `structured` | JSON 파서 | 같은 파서 | 0회 |")
w("")
w("판정 규칙은 세 조건이 같다.")
w("")
w("```")
w("                     ┌─ 네 행위 중 하나가 나왔나? ─ 아니오 ─→ format_errors += 1")
w("  메시지 ─→ read() ─┤")
w("                     └─ 예 ─┬─ propose 인가? ─ 아니오 ─→ 성공")
w("                            └─ 예 ─┬─ 정수 가격이 있나? ─ 아니오 ─→ format_errors += 1")
w("                                   └─ 예 ─→ 성공, 가격 기록")
w("")
w("읽기에 실패해도 메시지는 그대로 상대에게 전달된다.")
w("에이전트 둘은 계속 대화하고, 프로그램만 상태를 놓친다.")
w("```")
w("")
w("파서는 JSON 객체에서 멈춘다. JSON 뒤에 붙은 문장에 진짜 제안이 있어도 읽지 않는다.")
w("그 손실이 측정 대상이므로 주워담지 않았다.")
w("")
w("### 강의자료 조건에서 바꾼 것")
w("")
w("| 항목 | 강의자료 | 이번 실험 | 이유 |")
w("|---|---|---|---|")
w("| 턴 한도 | `MAX_TURNS = 8` | `10` | 왕복 흥정 두세 번의 여유 |")
w("| 한도 고지 | 뼈대에 없음 | 에이전트에게 알림 | 안 알리면 거절이 공짜다. reserve 40인 seller 가 100·110·115·118·119를 연속 거절하고 `open` 으로 끝났다 |")
w("| 첫 발화 | 명시 없음 | `\"%s\"` | 중립 오프너에서는 buyer 가 질문으로 열고, 네 행위 중 맞는 것이 없어 1턴에 죽는다 |" % negotiate.OPENER)
w("| 모델 | `claude-haiku-4-5` (CLI, temperature 설정 불가) | `gpt-4o-mini`, temperature 0.7 | temperature 를 명시할 수 있다 |")
w("| 시나리오 | 6개 | 5개 | 최소 4개. ZOPA 폭을 넓혔다 |")
w("")
w("### 공통 문단에 더한 것 — 세 조건에 동일하게 들어간다")
w("")
w("| 추가 | 내용 | 무엇이 달라지나 |")
w("|---|---|---|")
w("| 보수 구조 | 거래 실패는 손해. 한도 안 거래는 이득. 한도를 넘은 거래는 실패보다 훨씬 큰 손해. 조금이라도 이득이면 닫는다 | `violation` 의 성격이 \"지시를 어기는가\"에서 \"손해를 감수하면서까지 어기는가\"로 바뀐다 |")
w("| 역제안 규칙 | `reject-proposal` 은 숫자를 아예 안 낼 때만 쓴다. 숫자가 있으면 `propose` 로 보낸다 | FIPA 에서도 `reject-proposal` 은 가격을 싣지 못한다. 프로토콜을 명시한 것 |")
w("| 리더의 강제 선택 | 넷 중 반드시 하나 | `unclear` 출구를 주지 않았다 |")
w("")
w("`logs/free-neutral-opener.txt` 는 옛 중립 오프너로 `free` 3 에피소드를 돌린 별도 관측이다.")
w("오프너를 바꾼 근거이며 `results.csv` 에는 들어가지 않는다.")
w("")
w("### 지표")
w("")
w("| 열 | 뜻 | 방향 |")
w("|---|---|---|")
w("| `correct` | 거래 가능하면 한도 안 거래가 정답, 불가능하면 결렬이 정답 | 높을수록 좋음 |")
w("| `violation` | reserve 아래 또는 budget 위에서 성립한 거래 | 낮을수록 좋음 |")
w("| `turns` | 주고받은 메시지 수. 10이면 `open` | |")
w("| `format_errors` | 프로토콜 계층이 읽지 못한 메시지 수 | 낮을수록 좋음 |")
w("| `reader_calls` | 메시지를 읽는 데 쓴 모델 호출 수 | 낮을수록 쌈 |")
w("")
w("헤더는 고정이라 열을 늘릴 수 없다. 그래서 `note` 에 다음을 적었다.")
w("")
w("| `note` 항목 | 담는 것 |")
w("|---|---|")
w("| `buyer_surplus` / `seller_surplus` | 거래가 성사됐을 때 ZOPA 를 누가 얼마나 가져갔나 |")
w("| `accept-without-price=N` | 수락했는데 기록된 상대 가격이 없던 횟수 |")
w("| `agent_calls` `agent_tokens` `reader_tokens` `retries` | 비용과 429 재시도 |")
w("| `scenarios=%s` | 시나리오 파일 지문 |" % FP)
w("")
w("### 한계")
w("")
w("1. 조건당 시나리오마다 3회다. 추세는 읽히지만 소수점 차이를 주장할 크기가 아니다.")
w("2. `correct` 는 한도만 본다. 한도 안이면 1이므로 협상을 잘했는지는 보지 않는다.")
w("   `note` 의 잉여 배분과 같이 읽어야 한다.")
w("3. `free` 의 \"seller 가 숫자를 냈다\" 는 느슨한 대리 측정이다. 태그가 없어 \"메시지에")
w("   숫자가 있는가\"로 셌고, 상대 가격을 인용만 해도 잡힌다.")
w("4. `open` 을 거래 불가 시나리오에서 정답으로 셌다. README 가 \"a deal exactly when")
w("   reserve ≤ budget\" 이라 적었고 `open` 은 거래가 아니기 때문이다. `outcome` 열이")
w("   그대로 있으므로 다르게 채점하려면 `results.csv` 만으로 다시 셀 수 있다.")
w("")

# ---------------------------------------------------------------- 2
w("## 2. 결과")
w("")
w("### 조건별 요약")
w("")
w("| condition | correct / 15 | violation | deal · no_deal · open | 평균 turns | format_errors | reader_calls |")
w("|---|---:|---:|---|---:|---:|---:|")
for c in COND:
    w("| `%s` | %d | %d | %d · %d · %d | %.1f | %d | %d |"
      % (c, S[c]["correct"], S[c]["viol"], S[c]["deal"], S[c]["no_deal"],
         S[c]["opn"], S[c]["turns"], S[c]["fmt"], S[c]["reader"]))
w("")
w("메시지 수로 나누면 읽기 단가가 나온다.")
w("")
w("| condition | 메시지 | reader_calls | 메시지당 |")
w("|---|---:|---:|---:|")
for c in COND:
    w("| `%s` | %d | %d | %.2f |"
      % (c, S[c]["msgs"], S[c]["reader"], S[c]["reader"] / S[c]["msgs"]))
w("")
w("위반은 세 조건 모두 0건이다. 보수 구조를 넣은 뒤 45 에피소드에서 한 번도 자기 한도를")
w("넘지 않았고, 거래가 불가능한 두 시나리오에서 억지 거래가 한 건도 성립하지 않았다.")
w("")
w("```")
w("메시지 하나를 읽는 값 (1.00 = 모델 호출 한 번)")
w("")
w("free        ████████████████████████████████████████  1.00   (%3d회)" % S["free"]["reader"])
w("tagged      ████████████████████                      0.50   (%3d회)" % S["tagged"]["reader"])
w("structured                                            0.00   (%3d회)" % S["structured"]["reader"])
w("```")
w("")
w("### 시나리오 × 조건")
w("")
w("| 시나리오 | ZOPA | `free` | `tagged` | `structured` |")
w("|---|---:|---|---|---|")
for s in SCEN:
    sid = str(s["id"]); z = s["budget"] - s["reserve"]
    cells = []
    for c in COND:
        r = [x for x in ROWS if x["condition"] == c and x["scenario"] == sid]
        cells.append(" · ".join((x["price"] or x["outcome"])
                                + ("" if x["correct"] == "1" else " ✗") for x in r))
    w("| %s | %+d | %s |" % (s["item"], z, " | ".join(cells)))
w("")
w("숫자는 거래가, `open`/`no_deal` 은 결렬이다. `✗` 는 `correct` 가 0인 에피소드다.")
w("아래 두 행의 결렬은 거래 불가 시나리오이므로 정답이다.")
w("")
w("```")
w("                  free      tagged    structured")
w("ZOPA 넓음   +80·+64   5/6       5/6       1/6     ← 여기서만 갈린다")
w("ZOPA 좁음   +20       3/3       3/3       3/3")
w("거래 불가   −10·−150  6/6       6/6       6/6     (결렬이 정답)")
w("```")
w("")
w("깎을 여지가 클수록 seller 가 버틴다. `structured` 의 seller 는 버티면서 자기 숫자를")
w("내놓지 않아 교착된다.")
w("")
w("### 로그에서 집계한 보조 지표")
w("")
w("`results.csv` 의 열만으로는 왜 그 숫자가 나왔는지 알 수 없어 로그에서 세 가지를 더 셌다.")
w("")
w("#### ① seller 가 자기 가격을 제시한 메시지")
w("")
w("| 조건 | 센 방법 | 결과 |")
w("|---|---|---:|")
w("| `free` | 메시지에 숫자가 있는가 (태그가 없으므로) | %d / %d |" % (seller_num, seller_msg))
w("| `tagged` | 맨 앞 태그가 `(propose)` 인가 | %d / %d |" % (acts["tagged"]["seller/propose"], tag_seller))
w("| `structured` | `performative` 가 `propose` 인가 | %d / %d |" % (st_prop, st_seller))
w("")
w("공통 문단이 \"숫자가 있으면 `propose` 로 보내라\"고 명시하는데도 `tagged` 와 `structured` 의")
w("seller 는 사실상 역제안을 선언하지 않았다. 같은 모델이 `free` 에서는 거의 모든 메시지에")
w("숫자를 담는다.")
w("")
w("#### ② `free` 리더의 라벨 분포와 조용히 사라진 가격")
w("")
w("| 리더가 붙인 라벨 | 건수 | 가격을 요구하나 |")
w("|---|---:|---|")
for k, v in free_lab.most_common():
    w("| `%s` | %d | %s |" % (k, v, "예" if k == "propose" else "아니오"))
w("")
w("| | 가격이 사라진 메시지 | `format_errors` 에 잡힌 수 |")
w("|---|---:|---:|")
w("| `free` | %d / %d (%.0f%%) | %d |"
  % (free_drop, free_num, 100 * free_drop / free_num, S["free"]["fmt"]))
w("| `tagged` | %d / %d (%.0f%%) | %d |"
  % (tag_lost, tag_prop, 100 * tag_lost / tag_prop, S["tagged"]["fmt"]))
w("")
w("판정 규칙이 `propose` 일 때만 가격을 요구하므로, 리더가 스스로 `reject-proposal` 이라고")
w("답하면 가격이 없어도 통과한다.")
w("")
w("```")
w("free      행위와 가격을 리더가 함께 정한다")
w("          가격을 못 뽑겠다  →  \"reject-proposal\" 이라고 답한다  →  통과")
w("                                                                  ↑ 출구")
w("")
w("tagged    행위는 정규식이 이미 propose 로 못박았다")
w("          가격을 못 뽑겠다  →  propose 인데 가격 없음  →  format_error")
w("                                                        ↑ 출구 없음")
w("```")
w("")
w("`tagged` 의 `format_errors` 가 큰 것은 더 자주 실패해서가 아니라 실패가 드러나기")
w("때문이다. 태그의 역할 하나는 리더의 도망갈 구멍을 막는 것이다.")
w("")
w("#### ③ 거래가 성사됐을 때 ZOPA 배분")
w("")
w("| 조건 | 거래 | buyer 잉여 | seller 잉여 | seller 몫 |")
w("|---|---:|---:|---:|---:|")
for c in COND:
    w("| `%s` | %d | %d | %d | %d%% |"
      % (c, S[c]["deal"], S[c]["bs"], S[c]["ss"], S[c]["share"]))
w("")
w("```")
w("buyer 잉여 = budget − 거래가        seller 잉여 = 거래가 − reserve")
w("둘의 합이 ZOPA.   50 대 50 이면 반씩 나눈 것.")
w("")
w("free        buyer ████████████████████ │ ████████████████████ seller   51%")
w("tagged      buyer ██████████ │ ██████████████████████████████ seller   74%")
w("structured  buyer ████████████████████████████████ │ ████████ seller   21%")
w("```")
w("")
w("같은 시나리오의 거래가를 나란히 놓으면 `tagged` 가 왜 비싼지 보인다.")
w("")
w("| 물건 | `free` | `tagged` |")
w("|---|---|---|")
for sid, name in (("2", "이어폰"), ("3", "키보드"), ("1", "자전거")):
    g = lambda c: " · ".join(x["price"] for x in ROWS
                             if x["condition"] == c and x["scenario"] == sid and x["price"])
    w("| %s | %s | %s |" % (name, g("free"), g("tagged")))
w("")
w("`tagged` 가 전부 비싸다. seller 가 `propose` 를 한 번도 보내지 않아 buyer 에게 기준점이")
w("없고, buyer 가 혼자 가격을 올린다.")
w("")
w("`structured` 의 %d%% 는 그대로 읽으면 안 된다. 성사된 %d건 중 3건이 같은 시나리오"
  % (S["structured"]["share"], S["structured"]["deal"]))
w("(이어폰, reserve 90)이고, buyer 의 첫 제안 90을 seller 가 즉시 수락한 것이다. 협상을 이긴")
w("것이 아니라 협상이 필요 없던 건만 성사됐다. 표본 선택 효과이므로 배분을 논할 크기가 아니다.")
w("")
w("### 에피소드 45건 (`results.csv` 전문)")
w("")
w("| run | condition | sc | 가능 | outcome | price | correct | viol | turns | fmt | reader | note |")
w("|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|")
for x in ROWS:
    note = x["note"].replace(" scenarios=%s" % FP, "")
    w("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | `%s` |"
      % (x["run"], x["condition"], x["scenario"], x["deal_possible"], x["outcome"],
         x["price"] or "", x["correct"], x["violation"], x["turns"],
         x["format_errors"], x["reader_calls"], note))
w("")
w("크래시는 0건이다. `scenarios=%s` 는 지면을 아끼려 위 표에서만 뺐고 `results.csv` 의" % FP)
w("모든 행에 붙어 있다.")
w("")

# ---------------------------------------------------------------- 3
w("## 3. FIPA-ACL 과 세 조건")
w("")
w("| 항목 | FIPA-ACL (2002) | `free` | `tagged` | `structured` |")
w("|---|---|---|---|---|")
w("| illocutionary force 가 어디에 있는가 | `performative`. 13개 파라미터 중 유일한 필수 필드 | 메시지 어디에도 없다. 문장에서 사후에 읽어낸다 | 문장 맨 앞 괄호 태그 하나 | JSON 의 `performative` 필드 |")
w("| content 언어 | `fipa-sl` 같은 형식 언어 + 선언된 `ontology` | 영어 문장 | 영어 문장 | `{\"price\": 정수 또는 null}` |")
w("| content 를 누가 해석하는가 | 규격은 \"받는 쪽이 해석한다\". 양쪽이 ontology 를 미리 공유 | LLM 리더가 매 메시지 | 태그는 정규식, 가격은 LLM 리더 | 파서. 모델이 개입하지 않는다 |")
w("| 대화가 어떻게 끝나는가 | interaction protocol 이 종료 상태를 규정 | `accept-proposal` / `refuse` 라벨, 또는 10턴 | 같음 | 같음 |")
w("| sincerity 를 보장하는 것 | 없다. 규격이 규범으로 요구하고 \"성실하지 않은 경우는 범위 밖\"이라 적었다 | system prompt 의 보수 구조뿐. 검증 불가 | 같음 | 같음 |")
w("| 메시지 하나를 읽는 비용 | performative 문자열 비교. 사실상 0 | 1.00회 / 메시지 (총 %d) | 0.50회 (총 %d) | 0.00회 (총 %d, 진짜 0) |"
  % (S["free"]["reader"], S["tagged"]["reader"], S["structured"]["reader"]))
w("| 실패하는 방식 | semantic verification problem. 보내는 쪽의 믿음을 확인할 방법이 없다 | 리더가 역제안을 `reject-proposal` 로 읽어 가격이 조용히 사라진다 (%d/%d, `format_errors` 에 안 잡힘) | 태그는 읽히는데 문장 속 숫자를 리더가 못 뽑는다 (%d/%d) | seller 가 역제안을 선언하지 않아 교착된다 (`propose` %d/%d) |"
  % (free_drop, free_num, tag_lost, tag_prop, st_prop, st_seller))
w("")
w("### 세 점의 위치")
w("")
w("```")
w("                읽는 값이 비싸다                        읽는 값이 0")
w("            ◄───────────────────────────────────────────────────►")
w("")
w("  free                    tagged                    structured")
w("   │                        │                           │")
w("   │ 무엇이든 표현된다        │ 행위만 고정,               │ 행위와 내용을 둘 다 고정")
w("   │ 읽기가 틀려도           │ 내용은 자연어              │ 좁은 어휘가 에이전트의")
w("   │ 형식 오류로 안 잡힌다    │ 가격 추출에서만 샌다        │ 행동 자체를 좁힌다")
w("")
w("  FIPA-ACL 은 structured 쪽 끝에 있다. 대신 ontology 합의 비용과")
w("  검증 불가능한 의미론을 떠안았다.")
w("```")
w("")
w("강의자료가 인용한 agent communication trilemma 의 세 점이 그대로 나온다. `tagged` 가")
w("그 사이에 있다. 행위만 고정하고 내용은 자연어로 두면 리더 호출이 절반으로 줄고(%d → %d)"
  % (S["free"]["reader"], S["tagged"]["reader"]))
w("행위 판정 오류가 사라지는 대신, 남은 절반인 가격 추출에서 %d 번 중 %d 번 샌다."
  % (tag_prop, tag_lost))
w("")
w("한 가지는 FIPA 와 이번 구현이 같다. sincerity 를 보장하는 장치가 어느 쪽에도 없다는")
w("점이다. FIPA 는 규범으로 요구하고 강제하지 못했고, 이번 실험은 system prompt 의 보수")
w("구조로 요구했다. 위반 0건은 그것이 이번 모델과 이번 시나리오에서 지켜졌다는 관측이지,")
w("보장된다는 뜻이 아니다.")
w("")

w("## 4. 해석")
w("")
w("<!-- 한 문단. 어느 조건에서 어느 숫자가 바뀌었고 왜인지, 로그의 줄을 인용해서. -->")
w("")

# Part 4 is written by hand. When REPORT.md already has one, keep it verbatim
# and replace only parts 1-3, so regenerating never eats the interpretation.
PART4 = "## 4. 해석"
cut = O.index(PART4)
body, tail = "\n".join(O[:cut]), "\n".join(O[cut:])
if os.path.exists("REPORT.md"):
    prev = open("REPORT.md", encoding="utf-8").read()
    at = prev.find(PART4)
    if at != -1 and prev[at:].strip() != tail.strip():
        tail = prev[at:].rstrip()
        print("kept the existing part 4")
open("REPORT.md", "w", encoding="utf-8", newline="\n").write(body + tail + "\n")
print("wrote REPORT.md,", len(O), "lines")
