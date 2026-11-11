"""Investigator ("조사원", role analyst): works one question at a time for many steps and writes a finding (human code).

    python3 -m kernel.analyst --root R [--once] [--dry] [--harness DIR] [--cli PATH]

It changes nothing in the shop: no policy, no rule code, no forum post. It reuses the role operators' compact loop,
model path, tool running, masking and new_tool handling (kernel.operator.Operator) with its own prompt, done handling,
notes (a2/operator_notes_analyst.md) and state (a2/operator_state_analyst.json). Questions come from the role
operators' done.ask and from the standing questions of ANALYST_JOB.md (kernel.questions); findings go to
a2/findings.jsonl, which the role operators read in their next wake. Trajectory rows carry agent and role "analyst".
It never takes the operators' busy lock or the ledger lock, so it never delays an operator wake.

Env: A2_ANALYST_STEPS (default 60, 6 to 200 model calls per question; a silent backstop, not shown to the model),
A2_ANALYST_EFFORT (default medium), A2_ANALYST_MIN_GAP_S (default 900: at most one run per this many seconds; a
scheduler detail, not shown to the model).
"""

import argparse
import json
import os
import re
import time

from kernel import brief as b
from kernel import common as c
from kernel import experiments as ex
from kernel import operator as op
from kernel import agent_registry, questions, tools
from kernel.trajectory import Trajectory

ROLE = "analyst"
JOB = os.path.join(op.HERE, "ANALYST_JOB.md")
POLL_S = 60
MIN_GAP_S = 900
PAST_N = 5  # earlier findings shown in the prompt
# Tool section: names and one short line each; the full description and argument names come from tool_search (as a
# role operator sees its non-recommended tools). 11-12: the full list was a third of the fixed prompt.
TOOL_LINE = 90
TOOL_FULL = ("analyze",)  # shown with its whole description: its load() interface is what the analysis code needs
TOOL_MORE = "- 도구마다 설명 전체와 인자 이름은 tool_search(query=도구 이름)로 봄. 위 이름은 그대로 부를 수 있음"
_SENTENCE = re.compile(r"(?<=\.)\s")
REFUSED = (
    "policy",
    "code",
    "forum",
    "ask",
)  # done items an operator may send and the investigator may not
_new_tool = next(
    ln for ln in op.COMPACT_SCHEMA.splitlines() if ln.startswith('  "new_tool"')
)
_new_tool_rule = next(
    ln for ln in op.COMPACT_SCHEMA.splitlines() if ln.startswith("new_tool은")
)
SCHEMA = (
    """한 단계에 JSON 객체 하나로만 답함. 도구는 이 JSON의 도구 호출로만 불리고 커널이 돌림. 모델 자체의 함수 호출(exec, wait 등)은 이 환경에 실행기가 없어 실패함. 둘 중 하나:
1. 도구 호출: {"tools": [{"tool": "<도구 이름>", "args": {<인자>}}, ...], "keep": "<바로 앞 관찰에서 남길 결론>"}
2. 끝내기: {"done": {
  "answer": {"finding": "<확인한 원인과 숫자, 표본 수, 확인 못 한 것>", "evidence": ["<숫자와 그 숫자를 본 도구 이름>", ...], "suggest": "<운영자가 시험해 볼 값이나 코드 자리. 제안이지 결정이 아님>", "for": ["shop" 또는 "buyer", 둘 다면 둘]},
  "notes": "<다음 질문의 네가 읽을 메모 전체. 생략하면 그대로 둠>",
  "agent": {"op": "create|update|retire", "name": "<영소문자_숫자>", "job": "<이 에이전트의 일 문서 전체>", "wake": {"every_min": <분>, "on_ask": true}, "powers": ["read"]},
"""
    + _new_tool
    + """}}
answer는 꼭 냄. 확인하지 못한 것은 추측하지 않고 finding에 확인 못 함으로 적음.
"""
    + _new_tool_rule
    + """
policy, code, forum, ask는 받지 않음: 조사원은 가게를 바꾸지 않음. 값과 코드의 결정은 운영자 몫임
agent는 읽기 에이전트 하나를 만들거나(create), 일 문서와 깨는 때를 고치거나(update), 은퇴시킴(retire). 커널이 job 앞에 머리글(만든 쪽, 권한, 넘을 수 없는 선)을 붙여 따로 돌림. wake는 every_min(그 분마다 깸)과 on_ask(그 이름으로 온 질문에 깸) 중 하나 이상. 그 결과는 두 운영자의 조사 결과 절에 그 이름으로 감"""
)


def _short(doc):
    """The first sentence of a tool description, cut at a word boundary to about TOOL_LINE chars."""
    text = _SENTENCE.split(" ".join(str(doc).split()), 1)[0]
    if len(text) <= TOOL_LINE:
        return text
    return text[:TOOL_LINE].rsplit(" ", 1)[0] + " …"


def _steps():
    try:
        return max(6, min(200, int(os.environ.get("A2_ANALYST_STEPS") or 60)))
    except ValueError:
        return 60


def _min_gap():
    v = os.environ.get("A2_ANALYST_MIN_GAP_S", "")
    return int(v) if v.isdigit() else MIN_GAP_S


class Analyst(op.Operator):
    agent = ROLE

    def __init__(self, root, terra, traj, **kw):
        super().__init__(root, terra, traj, role=ROLE, **kw)
        self._question = None

    # ------------------------------------------------------------ what differs from a role operator
    def _seed_notes(
        self,
    ):  # its notes start empty, not as a copy of the single operator's
        pass

    def compact(
        self,
    ):  # made tools callable, harness tools refused, whether LIVE is on or not
        return True

    def _steps(self):
        return _steps()

    def _effort(self, route):
        return os.environ.get("A2_ANALYST_EFFORT") or "medium"  # 11-11 성수님: 추론 강도는 모두 medium

    def _early_done(self, obs_all):
        # 11-11 22:08: question #7 was answered in one step with no tool call ("도구 실행 환경 오류", no such error)
        if not obs_all:
            return "거절됨: 도구를 한 번도 부르지 않고 답할 수 없음. 도구 결과를 먼저 읽을 것 (도구 오류를 봤다면 그 오류 글을 그대로 적음)"
        return None

    def _tool_extra(self, name, args, obs):  # it has no recommended list
        return {}

    def _tool_text(self, listed):
        lines = [f"- {name}: {doc if name in TOOL_FULL else _short(doc)}" for name, doc in listed]
        return "\n".join(lines + [TOOL_MORE])

    # ------------------------------------------------------------ one question
    def _job(self):
        try:
            with open(JOB, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def enqueue_standing(self, now):
        return questions.enqueue_standing(
            self.root, questions.standing(self._job()), now
        )

    def _past(self):
        lines = []
        for r in reversed(questions.findings(self.root)[-PAST_N:]):
            mark = " (미완)" if r.get("status") == "failed" else ""
            lines.append(
                f"- {c.kst(r.get('ts') or 0, '%m-%d %H:%M')} #{r.get('id')}{mark} 질문: "
                + " ".join(str(r.get("q")).split())[:150]
                + "\n  결과: "
                + " ".join(str(r.get("finding")).split())[:300]
            )
        return "\n".join(lines)

    def _base(self, q, now):
        return self._base_sized(q, now)[0]

    def _base_sized(self, q, now):
        """The fixed prompt of question q and its chars by section (kernel.operator.sized)."""
        try:
            extra = list(ex.lines(self.root, now))
        except Exception:  # the ledger only adds lines to the card
            extra = []
        who = {
            "shop": "가게 운영자",
            "buyer": "구매 운영자",
            "human": "사람",
            "standing": "정기 질문 (조사원 일 문서)",
            "auditor": "감사자 (운영자가 못 보는 사실을 메우려는 요청. 도구를 만들어 달라는 것이면 analyze로 돌려 본 뒤 new_tool로 저장함)",
        }
        return op.sized(
            [
                self._job(),
                tools.guide_always(),
                "## 상태 카드 (커널이 모은 사실, 해석 없음)",
                b.build_compact(self.root, now, None, extra=extra),
                "## 이번 질문",
                f"#{q['id']} {c.kst(q.get('ts') or now, '%m-%d %H:%M')} 물은 쪽 {who.get(q.get('from'), q.get('from'))}, "
                f"받는 쪽 {','.join(q.get('for') or [])}\n"
                + c.mask(str(q.get("q")), self.secrets),
                "## 지난 조사 결과 (최근 5건, 짧게. 전문은 findings 도구)",
                c.mask(self._past(), self.secrets) or "없음",
                "## 네 메모 (지난 질문의 네가 남김)",
                c.mask(self._notes(), self.secrets) or "없음",
                "## 도구",
                self._tool_text(self.listed()),
                "## 답 형식",
                SCHEMA,
            ]
        )

    def run(self, q, now):
        """Work question q (a questions row) to a finding. -> the run summary (also an analyst_run row)."""
        self._question, self._kept = q, []
        st = self._state()
        st.update(last_run_ts=now, last_q=q["id"])
        self._save_state(st)
        loaded, _ = self._always_text()  # the harness only routes the call (timeout)
        route = (loaded[0].routing.get("operator.wake") or {}) if loaded else {}
        reason = f"질문 #{q['id']}"
        try:
            base, sizes = self._base_sized(q, now)
            return self._compact_loop(base, route, reason, now, sizes)
        except Exception as e:  # a crashed run still answers the asker
            return self._end(
                reason, 0, "crash", {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            )

    def _finish_compact(self, done, now, read_files=()):
        """Apply the investigator's done: the answer becomes a finding and closes the question; notes and new_tool go
        through the operators' code; policy, code, forum and ask are refused and logged."""
        refused = [k for k in REFUSED if k in done]
        if refused:
            self.traj.append(
                self.agent, "analyst_refused", qid=self._question["id"], keys=refused
            )
        result = self._finish_compact_locked(
            {k: done[k] for k in ("new_tool", "notes", "note") if k in done}, now, ()
        )
        if refused:
            result["refused"] = refused
        if "agent" in done:  # a read-only agent the investigator defines (kernel.agent_registry)
            result["agent"] = agent_registry.apply(self.root, ROLE, done["agent"], now, self.traj, self.secrets)
        ans = done.get("answer")
        if (
            isinstance(ans, dict)
            and isinstance(ans.get("finding"), str)
            and ans["finding"].strip()
        ):
            m = lambda v: c.mask(str(v), self.secrets)
            ev = ans.get("evidence") if isinstance(ans.get("evidence"), list) else []
            questions.add_finding(
                self.root,
                self._question,
                m(ans["finding"]),
                [m(e) for e in ev],
                m(ans.get("suggest") or ""),
                ans.get("for"),
                now,
            )
            questions.close(self.root, self._question["id"], "done", now)
            result["answer"] = {"ok": True, "id": self._question["id"]}
        else:
            result["answer"] = {"ok": False, "error": "answer.finding 없음"}
            self._fail("done에 answer.finding이 없음", now)
        return result

    def _fail(self, why, now):
        """Close the question as failed with a finding of what was checked (the kept conclusions)."""
        q = self._question
        if not any(r["id"] == q["id"] and r.get("status") == "open" for r in questions.load(self.root)):
            return  # already answered (or closed by hand)
        checked = (
            " / ".join(" ".join(str(k).split()) for k in self._kept) or "남긴 결론 없음"
        )
        text = c.mask(
            f"조사 미완 ({why}). 확인한 것: {checked}. 남은 것: 질문의 답은 확인 못 함",
            self.secrets,
        )
        questions.add_finding(
            self.root,
            q,
            text,
            [],
            "",
            None,
            now,
            status="failed",
        )
        questions.close(self.root, q["id"], "failed", now)

    def _end(self, reason, steps, status, result):
        if status != "done":
            why = {
                "no_done": "조사 미완 (답을 내지 못하고 끝남)",
                "model_error": "모델 호출 실패",
                "crash": "실행 오류",
            }.get(status, status)
            try:
                self._fail(why, c.now_ts())
            except Exception:  # the run row below is still written
                pass
        self.traj.append(
            self.agent,
            "analyst_run",
            qid=(self._question or {}).get("id"),
            reason=reason,
            steps=steps,
            status=status,
            result=result,
        )
        return dict(result, status=status, steps=steps)

    def poll(self, now, force=False):
        """Enqueue due standing questions, then run the next open one unless the last run is younger than
        A2_ANALYST_MIN_GAP_S (force: ignore the gap). -> the run summary or None."""
        self.enqueue_standing(now)
        last = self._state().get("last_run_ts")
        if not force and isinstance(last, (int, float)) and now - last < _min_gap():
            return None
        q = questions.next_open(self.root)
        return self.run(q, now) if q else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    ap.add_argument(
        "--once", action="store_true", help="one poll now (the gap ignored), then exit"
    )
    ap.add_argument("--dry", action="store_true", help="stub model")
    ap.add_argument("--harness", default=op.HARNESS)
    ap.add_argument("--cli", default=op.CLI)
    a = ap.parse_args(argv)
    root = c.data_root(a.root)
    login = c.owner_login(root)
    secrets = (login,) if login else ()
    traj = Trajectory(
        os.path.join(root, "a2/trajectory.jsonl"), secrets=secrets, role=ROLE
    )
    if a.dry:
        terra = op.DryTerra()
    else:
        from kernel.llm import Terra

        terra = Terra(traj, cli=a.cli, secrets=secrets)
    an = Analyst(root, terra, traj, harness_root=a.harness, dry=a.dry)
    poll_err = (None, 0.0)
    while True:
        try:
            res = an.poll(c.now_ts(), force=a.once)
            if res is not None:
                print(json.dumps(res, ensure_ascii=False, default=str), flush=True)
        except Exception as err:  # one bad poll must not stop the daemon; the same error is logged once an hour
            if a.once:
                raise
            name, t = type(err).__name__, time.time()
            if name != poll_err[0] or t - poll_err[1] >= 3600:
                poll_err = (name, t)
                traj.append(
                    ROLE,
                    "analyst_poll",
                    status="crash",
                    error=name,
                    detail=str(err)[:200],
                )
        if a.once:
            return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
