"""Runner of the agents that agents create (human code): one process wakes every live agent of a2/agents.json when it
is due, one at a time.

    python3 -m kernel.agents --root R [--once] [--dry] [--harness DIR] [--cli PATH]

An agent is due when a question addressed to it is open (wake.on_ask, kernel.questions "to") or its interval has
passed since its last wake (wake.every_min; nothing wakes more often than the poll, POLL_S). The registry is read at
every poll, so a create, update or retire takes effect without a restart. Each wake reuses the shared compact loop of
kernel.operator.Operator (compaction, keep, notes, recall, tool_search, analyze, new_tool, prompt-size records) with the
agent's own prompt (kernel.agent_registry.header, then the creator's job text), notes (a2/agent_notes_<name>.md) and
state (a2/agent_state_<name>.json). Trajectory rows carry the agent's name as agent; a wake ends in one agent_wake row.

Powers: done items outside the agent's powers are refused and logged (agent_refused). policy, forum and code go
through the operator's own done handling with the creator's role, so the same role checks, bounds, ledger, forum and
code gates apply; an agent holding any of them takes the operators' busy lock (role "agent:<name>") for its wake, so it
never runs beside an operator wake. answer becomes a finding marked with the agent's name, for the asker of the
question that woke it or for the agent's readers (registry "for"). A crash in one agent's wake is logged and the
runner goes on with the next agent.
"""

import argparse
import json
import math
import os
import time

from kernel import agent_registry as reg
from kernel import analyst as an
from kernel import brief as b
from kernel import code_edit
from kernel import common as c
from kernel import experiments as ex
from kernel import operator as op
from kernel import questions, tools
from kernel.trajectory import Trajectory

POLL_S = reg.POLL_S
BUSY_PREFIX = "agent:"  # role of a created agent's busy lock (kernel.operator.claim_busy)
PAST_N = 3  # its own earlier findings shown in its prompt
WRITE_KEYS = ("policy", "forum", "code")
ALWAYS_KEYS = ("answer", "notes", "note", "new_tool")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def schema(row):
    """The answer format a created agent sees: answer, notes, new_tool and note always; policy, forum and code lines
    (and their rules) only for the powers it has, taken from its creator's role schema."""
    side, powers = row.get("side"), row["powers"]
    lines = (op.role_schema(side) if side in op.ROLES else op.COMPACT_SCHEMA).splitlines()

    def pick(prefix):
        return [ln for ln in lines if ln.startswith(prefix)]

    items = ['  "answer": {"finding": "<확인한 것과 숫자, 표본 수, 확인 못 한 것>", "evidence": ["<숫자와 그 숫자를 본 도구 이름>", ...], '
             '"suggest": "<읽는 쪽이 시험해 볼 것. 제안이지 결정이 아님>"},']
    rules = ["done의 모든 항목은 선택임. 질문에 답하는 깨움이면 answer를 꼭 냄. 확인하지 못한 것은 추측하지 않고 finding에 확인 못 함으로 적음."]
    writes = side in op.ROLES
    if writes and "policy" in powers:
        items += pick('  "policy"')
        rules += (["지금 값과 범위(최소, 최대)는 policy 도구로 봄. 범위 밖 값은 거절됨."] + pick("policy 항목은") + pick("split은")
                  + pick("진행 중인 실험") + pick("policy 키:"))
    if writes and "forum" in powers:
        items += pick('  "forum"')
    if writes and "code" in powers:
        items += pick('  "code"')
        rules += pick("code는") + pick("커널이 규칙 코드의") + pick("적용 뒤 커널이")
    items += ['  "notes": "<다음 깨움의 네가 읽을 메모 전체. 생략하면 그대로 둠>",'] + pick('  "new_tool"')
    items.append('  "note": "<이번 깨움 한 줄 요약>"}}')
    rules += pick("new_tool은") + ["머리글의 권한 밖 항목은 커널이 거절하고 기록함"]
    return "\n".join(['한 단계에 JSON 객체 하나로만 답함. 도구는 이 JSON의 도구 호출로만 불리고 커널이 돌림. 모델 자체의 함수 호출(exec, wait 등)은 이 환경에 실행기가 없어 실패함. 둘 중 하나:',
                      '1. 도구 호출: {"tools": [{"tool": "<도구 이름>", "args": {<인자>}}, ...], "keep": "<바로 앞 관찰에서 남길 결론>"}',
                      '2. 끝내기: {"done": {'] + items + rules)


class CreatedAgent(op.Operator):
    """One created agent's wake. role is its creator's side when it holds a write power (the gates key on it), else
    its own name (its made tools are tagged with it)."""

    def __init__(self, root, terra, traj, row, harness_root=op.HARNESS, dry=False):
        self.row = row
        self.agent = row["name"]
        self.writes = bool(row.get("side") in op.ROLES and set(row["powers"]) & set(WRITE_KEYS))
        super().__init__(root, terra, traj, harness_root=harness_root, dry=dry,
                         role=row["side"] if self.writes else row["name"])
        self.state_path = reg.state_path(root, row["name"])
        self._question, self._reason, self._extra = None, "", []

    # ------------------------------------------------------------ what differs from a role operator
    def _seed_notes(self):  # its notes start empty
        pass

    def _notes_path(self):
        return reg.notes_path(self.root, self.row["name"])

    def compact(self):
        return True

    def _steps(self):
        return op.COMPACT_STEPS

    def _effort(self, route):
        return "medium"

    def _early_done(self, obs_all):
        if not obs_all:
            return "거절됨: 도구를 한 번도 부르지 않고 끝낼 수 없음. 도구 결과를 먼저 읽을 것 (도구 오류를 봤다면 그 오류 글을 그대로 적음)"
        return None

    def _tool_extra(self, name, args, obs):
        return {}

    def _tool_text(self, listed):
        return an.Analyst._tool_text(self, listed)

    # ------------------------------------------------------------ when
    def due(self, now):
        """(reason, question or None) when this agent should wake now, else None."""
        wake = self.row["wake"]
        if wake.get("on_ask"):
            q = questions.next_open(self.root, to=self.row["name"])
            if q:
                return f"질문 #{q['id']}", q
        every = wake.get("every_min")
        if _num(every) and every > 0:
            last = self._state().get("last_wake_ts")
            if not _num(last) or now - last >= max(POLL_S, every * 60):
                return f"주기 깨움 ({every:g}분마다)", None
        return None

    # ------------------------------------------------------------ prompt
    def _past(self):
        rows = [r for r in questions.findings(self.root) if r.get("by") == self.row["name"]][-PAST_N:]
        return "\n".join(f"- {c.kst(r.get('ts') or 0, '%m-%d %H:%M')}{' (미완)' if r.get('status') == 'failed' else ''}: "
                         + " ".join(str(r.get("finding")).split())[:300] for r in reversed(rows))

    def _base_sized(self, q, reason, now, last_brief=None):
        """The fixed prompt of this wake and its chars by section (kernel.operator.sized)."""
        row, kw = self.row, ({"role": self.row["side"]} if self.writes else {})
        try:
            extra = list(ex.lines(self.root, now, **kw))
        except Exception:  # the ledger only adds lines to the card
            extra = []
        if self.writes and "code" in row["powers"]:
            extra += list(code_edit.lines(self.root, now, **kw))
        if q is not None:
            who = reg.WHO_KO.get(q.get("from"), q.get("from"))
            ask = ["## 이번 질문", f"#{q['id']} {c.kst(q.get('ts') or now, '%m-%d %H:%M')} 물은 쪽 {who}\n"
                   + c.mask(str(q.get("q")), self.secrets)]
        else:
            ask = ["## 깨운 이유", reason]
        st = self._state()
        return op.sized([
            c.mask(reg.header(row), self.secrets),
            f"## 만든 쪽이 쓴 일 ({reg.WHO_KO.get(row.get('creator'), row.get('creator'))}가 씀, 판 {row['version']})",
            c.mask(reg.demote(row["job"]), self.secrets),
            tools.guide_always(),
            "## 상태 카드 (커널이 모은 사실, 해석 없음)",
            b.build_compact(self.root, now, last_brief, extra=extra),
            "## 지난 깨움에 네가 낸 것의 결과 (커널 기록. 거절된 것은 적용되지 않았음)",
            op._last_done_text((st.get("last_done") or {}).get("lines")) or "없음",
            *ask,
            "## 네 지난 결과 (최근 3건, 짧게. 전문은 agents 도구)",
            c.mask(self._past(), self.secrets) or "없음",
            "## 네 메모 (지난 깨움의 네가 남김)",
            c.mask(self._notes(), self.secrets) or "없음",
            "## 도구",
            self._tool_text(self.listed()),
            "## 답 형식",
            schema(row),
        ])

    # ------------------------------------------------------------ one wake
    def run(self, q, reason, now):
        """One wake (q: the question that woke it, or None). -> the wake summary (also an agent_wake row)."""
        self._question, self._reason, self._kept, self._extra = q, reason, [], []
        st = self._state()
        last_brief = st.get("last_brief_ts")
        st.update(last_wake_ts=now, last_brief_ts=now, **({"last_q": q["id"]} if q else {}))
        self._save_state(st)
        loaded, _ = self._always_text()  # the harness only routes the call (timeout)
        route = (loaded[0].routing.get("operator.wake") or {}) if loaded else {}
        try:
            base, sizes = self._base_sized(q, reason, now, last_brief)
            return self._compact_loop(base, route, reason, now, sizes)
        except Exception as e:  # a crashed wake still answers the asker
            return self._end(reason, 0, "crash", {"error": f"{type(e).__name__}: {str(e)[:200]}"})

    def _finish_compact(self, done, now, read_files=()):
        """Items within its powers go through the operator's done handling (the creator's gates); the rest are refused
        and logged. answer becomes a finding marked with its name."""
        allowed = set(ALWAYS_KEYS) | ({k for k in WRITE_KEYS if k in self.row["powers"]} if self.writes else set())
        refused = [str(k)[:40] for k in done if k not in allowed]
        if refused:
            self.traj.append(self.agent, "agent_refused", keys=refused[:12], powers=self.row["powers"],
                             qid=(self._question or {}).get("id"))
            self._extra.append(f"- 권한 밖이라 거절됨: {', '.join(refused[:12])}")
        result = op.Operator._finish_compact(self, {k: v for k, v in done.items() if k in allowed and k != "answer"},
                                             now, read_files)
        if refused:
            result["refused"] = refused[:12]
        ans, q = done.get("answer"), self._question
        if isinstance(ans, dict) and isinstance(ans.get("finding"), str) and ans["finding"].strip():
            m = lambda v: c.mask(str(v), self.secrets)
            ev = ans.get("evidence") if isinstance(ans.get("evidence"), list) else []
            base_q = q or {"id": None, "from": "agent", "q": self._reason, "for": self.row["for"]}
            questions.add_finding(self.root, base_q, m(ans["finding"]), [m(e) for e in ev], m(ans.get("suggest") or ""),
                                  None if q else self.row["for"], now, by=self.row["name"])
            if q:
                questions.close(self.root, q["id"], "done", now)
            result["answer"] = {"ok": True, **({"id": q["id"]} if q else {})}
            self._extra.append("- 조사 결과: 실림")
        elif q is not None:
            result["answer"] = {"ok": False, "error": "answer.finding 없음"}
            self._fail("done에 answer.finding이 없음", now)
        return result

    def _fail(self, why, now):
        """Close the question that woke it as failed with a finding of what was checked."""
        q = self._question
        if q is None or not any(r["id"] == q["id"] and r.get("status") == "open" for r in questions.load(self.root)):
            return
        checked = " / ".join(" ".join(str(k).split()) for k in self._kept or []) or "남긴 결론 없음"
        text = c.mask(f"조사 미완 ({why}). 확인한 것: {checked}. 남은 것: 질문의 답은 확인 못 함", self.secrets)
        questions.add_finding(self.root, q, text, [], "", None, now, status="failed", by=self.row["name"])
        questions.close(self.root, q["id"], "failed", now)

    def _end(self, reason, steps, status, result):
        if status != "done":
            why = {"no_done": "끝내지 못하고 끝남", "model_error": "모델 호출 실패",
                   "crash": "실행 오류"}.get(status, status)
            try:
                self._fail(why, c.now_ts())
            except Exception:  # the wake row below is still written
                pass
        self.traj.append(self.agent, "agent_wake", qid=(self._question or {}).get("id"), reason=reason, steps=steps,
                         status=status, result=result)
        try:  # the creator's prompt line and the next wake's record
            st = self._state()
            note = " ".join(str((result or {}).get("note") or "").split())
            st["last_line"] = (note or {"done": "끝남 (요약 없음)", "no_done": "done 없이 끝남", "model_error": "모델 오류",
                                        "crash": "실행 오류"}.get(status, str(status)))[:reg.LINE_CHARS]
            if self._extra and isinstance(st.get("last_done"), dict) and isinstance(st["last_done"].get("lines"), list):
                st["last_done"]["lines"] += self._extra
            self._save_state(st)
        except Exception:  # a record; it must never cost the wake
            pass
        return dict(result, status=status, steps=steps)


def poll(root, terra, secrets=(), now=None, harness_root=op.HARNESS, dry=False):
    """Wake every due live agent once, one at a time. -> [(name, wake summary)]. Never raises: a crash in one agent's
    wake is an agent_wake row with status crash, and the runner goes on."""
    out, path = [], os.path.join(root, "a2/trajectory.jsonl")
    try:
        rows = reg.live(root)[:reg.MAX_LIVE]
    except Exception:  # a broken registry is no agent
        rows = []
    for row in rows:
        t = c.now_ts() if now is None else now
        writes = row.get("side") in op.ROLES and bool(set(row["powers"]) & set(WRITE_KEYS))
        traj, q = Trajectory(path, secrets=secrets, role=row["side"] if writes else None), None
        try:
            ag = CreatedAgent(root, terra, traj, row, harness_root=harness_root, dry=dry)
            d = ag.due(t)
            if d is None:
                continue
            reason, q = d
            if ag.writes and not op.claim_busy(root, BUSY_PREFIX + row["name"], t):
                continue  # an operator's wake runs: this one waits for the next poll
            try:
                out.append((row["name"], ag.run(q, reason, t)))
            finally:
                if ag.writes:
                    op.release_busy(root, BUSY_PREFIX + row["name"])
        except Exception as e:  # one agent must not stop the others
            try:
                traj.append(row["name"], "agent_wake", status="crash", error=type(e).__name__, detail=str(e)[:200],
                            qid=(q or {}).get("id"))
                st = reg.state(root, row["name"])
                st["last_wake_ts"], st["last_line"] = t, "실행 오류"
                with open(reg.state_path(root, row["name"]) + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(st, f, ensure_ascii=False)
                os.replace(reg.state_path(root, row["name"]) + ".tmp", reg.state_path(root, row["name"]))
                if q is not None and any(r["id"] == q["id"] and r.get("status") == "open" for r in questions.load(root)):
                    questions.add_finding(root, q, "조사 미완 (실행 오류)", [], "", None, t, status="failed", by=row["name"])
                    questions.close(root, q["id"], "failed", t)
            except Exception:  # the record must not stop the runner either
                pass
            out.append((row["name"], {"status": "crash", "error": type(e).__name__}))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--once", action="store_true", help="one poll now, then exit")
    ap.add_argument("--dry", action="store_true", help="stub model")
    ap.add_argument("--harness", default=op.HARNESS)
    ap.add_argument("--cli", default=op.CLI)
    a = ap.parse_args(argv)
    root = c.data_root(a.root)
    login = c.owner_login(root)
    secrets = (login,) if login else ()
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets=secrets)
    if a.dry:
        terra = op.DryTerra()
    else:
        from kernel.llm import Terra

        terra = Terra(traj, cli=a.cli, secrets=secrets)
    while True:
        for name, res in poll(root, terra, secrets, harness_root=a.harness, dry=a.dry):
            print(json.dumps({"agent": name, **res}, ensure_ascii=False, default=str), flush=True)
        if a.once:
            return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
