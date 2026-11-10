"""Operator: wake conditions, the tool loop with Terra and the effects of its done action (human code).

    python3 -m kernel.operator --root R [--once] [--dry] [--harness DIR] [--cli PATH]

The kernel only bounds the operator's own settings, runs tools and applies edits through kernel.versions. What to
change and why is the model's (harness/operator text). Each wake: one prompt per step with the operator's always-read
harness, MARKET_BRIEF.md, the briefing (version results and call refusals near the top), the wake reason, the tool
list and the action schema; the model answers one JSON action per step. --dry swaps the model for a stub that
answers {"done": {}} at once.
"""

import argparse
import importlib
import json
import os
import subprocess
import sys
import time

from kernel import brief as b
from kernel import common as c
from kernel import harness as hz
from kernel import policy_edit, tools, versions
from kernel.agent import ParseError, parse_last_object
from kernel.llm import ModelError, ModelRefused
from kernel.trajectory import Trajectory

RESTOCK_WAKE_S = 600  # a missing or expired restock plan wakes the operator at most this often

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(HERE, "harness")
MARKET_BRIEF = os.path.join(HERE, "MARKET_BRIEF.md")
CLI = "/home/sungsoo/.local/bin/codex"  # 11-07 오후: codex 한도 초기화로 gpt-6.1-terra 복귀 (kernel/llm.py)
POLL_S = 60
BRIEF_CHARS = 24_000  # about 6k tokens (DESIGN 8, estimate)
OBS_TOTAL_CHARS = 120_000  # about 30k tokens of observations per wake (DESIGN 8, estimate)
EDIT_TARGETS = ("seller", "buyer", "operator")
BOUNDS = {
    "interval_s": (300, 7200, 1800),
    "wake_after_requests": (30, 5000, 150),
    "max_steps": (1, 30, 12),
    "breakage_n": (versions.BREAKAGE_BOUNDS[0], versions.BREAKAGE_BOUNDS[1], versions.BREAKAGE_DEFAULT),
    "rank_jump": (1, 50, 3),
    "bankrupt_near_factor": (1.0, 20.0, 2.0),
}
RESERVED_ARGS = ("harness_root", "dry", "now")
HARNESS_TOOLS = ("versions", "harness_read", "replay_probe")

ACTION_SCHEMA = """한 단계에 JSON 객체 하나로만 답함. 둘 중 하나:
1. 도구 호출: {"tool": "<도구 이름>", "args": {<인자>}}
2. 끝내기: {"done": {
  "reflection": [{"target": "seller|buyer|operator", "version": <판 번호>, "effects": [{"edit": "<수정 요약>", "effect": "도움|해로움|효과 없음|판단 불가", "evidence": "<근거>"}], "lesson": "<교훈 하나 또는 null>"}],
  "edits": [{"target": "seller|buyer|operator", "files": {"<상대 경로>": "<파일 전체 내용 또는 null(삭제)>"}, "reason": "<근거: 궤적의 어느 순간, 어떤 숫자>"}],
  "revert": [{"target": "seller|buyer|operator", "version": <돌아갈 판>, "reason": "<이유>"}],
  "trial": {"target": "seller", "files": {"<상대 경로>": "<파일 전체 내용 또는 null>"}, "share": <후보로 보낼 새 요청 비율 0.1~0.9>, "reason": "<근거>"},
  "trial_end": {"target": "seller", "adopt": <true면 후보를 지금 판으로 설치, false면 버림>, "reason": "<근거>"},
  "settings": {"interval_s": <초>, "wake_after_requests": <건수>, ...},
  "restock_plan": {"buy": [{"sku": "<SKU>", "qty": <양의 정수>}], "valid_until": <유닉스 시각>},
  "policy": [{"section": "defaults.food|defaults.goods|<상품군 전체 경로>", "key": "<policy 도구의 편집 가능 키>", "value": <수>, "reason": "<근거: 어떤 숫자>"}],
  "forum": [{"post": "<글>", "as": "shop|buyer"}],
  "memcheck": <true|false>,
  "note": "<이번 점검 요약>"}}
done의 모든 항목은 선택임.
trial은 판매자 후보 판을 지금 판과 같은 시간에 나란히 돌림: 새 판매 요청이 task id 해시로 두 판에 무작위로 나뉨. 진행 중에는 판매자 edits와 revert가 막히고, trial_end로 채택하거나 버림. 브리핑의 [동시 비교] 줄에 두 판 숫자가 나옴."""


def bounded(settings):
    out = {}
    for k, (lo, hi, default) in BOUNDS.items():
        v = settings.get(k) if isinstance(settings, dict) else None
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            v = default
        v = max(lo, min(hi, v))
        out[k] = int(v) if isinstance(lo, int) else float(v)
    return out


class DryTerra:
    """Stand-in model for --dry: ends every wake at once with no edits."""

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        return json.dumps({"done": {"edits": [], "reflection": [], "note": "dry"}})


class Operator:
    def __init__(self, root, terra, traj, harness_root=HARNESS, market_brief_path=MARKET_BRIEF, dry=False,
                 spawn=subprocess.Popen):
        self.root, self.terra, self.traj = root, terra, traj
        self.harness_root, self.market_brief_path, self.dry, self.spawn = (
            harness_root,
            market_brief_path,
            dry,
            spawn,
        )
        self.login = c.owner_login(root)
        self.secrets = (self.login,) if self.login else ()
        self.state_path = os.path.join(root, "a2/operator_state.json")

    # ------------------------------------------------------------ settings and state
    def settings(self):
        s = dict(c.load_json(os.path.join(self.harness_root, "operator", "settings.json"), {}) or {})
        env = os.environ.get("A2_OPERATOR_INTERVAL_S", "")  # 2026-11-10: the human's interval wins over the harness
        if env.isdigit():
            s["interval_s"] = int(env)
        return bounded(s)

    def _state(self):
        st = c.load_json(self.state_path, {})
        return st if isinstance(st, dict) else {}

    def _save_state(self, st):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, self.state_path)

    # ------------------------------------------------------------ wake conditions
    def _rank(self):
        snaps = b.board_snapshots(self.root)
        if not snaps:
            return None
        P = (c.load_json(snaps[-1][1], {}) or {}).get("players") or []
        E = sorted(P, key=lambda p: -(p.get("total") or 0))
        return next((i + 1 for i, p in enumerate(E) if c.is_mine(p, self.login)), None)

    def _shop_missing(self):
        sh = c.load_json(os.path.join(self.root, ".market/shops.json"))
        rows = sh if isinstance(sh, list) else list(sh.values()) if isinstance(sh, dict) else []
        if not rows:
            return False
        return not any(isinstance(r, dict) and c.is_mine(r, self.login) for r in rows)

    def _bankrupt_near(self, factor):
        inv = c.load_json(os.path.join(self.root, ".market/inventory.json"), {}) or {}
        bal, line = inv.get("balance"), inv.get("bankrupt_line")
        if not isinstance(bal, (int, float)) or not isinstance(line, (int, float)):
            return False
        return bal <= (line * factor if line > 0 else line)

    def _last_edit_ts(self, target):
        ts = 0.0
        for r in c.tail_json(os.path.join(self.root, "a2/trajectory.jsonl"), 20_000_000, b'"harness_'):
            if r.get("kind") in ("harness_edit", "harness_revert") and r.get("target") == target:
                ts = max(ts, r.get("ts") or 0)
        return ts

    def seller_requests_since(self, ts):
        return sum(
            1
            for r in c.tail_json(os.path.join(self.root, "a2/trajectory.jsonl"), 20_000_000, b'"seller.open"')
            if r.get("kind") == "decision"
            and r.get("agent") == "seller"
            and r.get("decision_kind") == "seller.open"
            and (r.get("ts") or 0) > ts
        )

    def events_now(self, s):
        return {
            "rank": self._rank(),
            "shop_missing": self._shop_missing(),
            "bankrupt_near": self._bankrupt_near(s["bankrupt_near_factor"]),
        }

    def wake_reason(self, now=None):
        """Why the operator should wake now, or None. Flags that cleared are saved so the next onset wakes again."""
        now = c.now_ts() if now is None else now
        s, st = self.settings(), self._state()
        ev = self.events_now(s)
        reasons = []
        last = st.get("last_wake_ts")
        if last is None or now - last >= s["interval_s"]:
            reasons.append("주기 점검")
        n = self.seller_requests_since(max(last or 0, self._last_edit_ts("seller")))
        if n >= s["wake_after_requests"]:
            reasons.append(f"마지막 수정 또는 점검 뒤 판매 요청 {n}건")
        if ev["rank"] and st.get("last_rank") and abs(ev["rank"] - st["last_rank"]) >= s["rank_jump"]:
            reasons.append(f"Everyone 순위 {st['last_rank']}위에서 {ev['rank']}위로 바뀜")
        if self.live() and b.restock_plan_state(self.root, now)[0] != "valid" and (
            last is None or now - last >= RESTOCK_WAKE_S
        ):
            reasons.append("재입고 계획 없음 또는 만료")
        if ev["shop_missing"] and not st.get("shop_missing"):
            reasons.append("우리 가게가 가게 목록에서 빠짐")
        if ev["bankrupt_near"] and not st.get("bankrupt_near"):
            reasons.append("잔고가 파산선 가까이 내려감")
        cleared = [k for k in ("shop_missing", "bankrupt_near") if st.get(k) and not ev[k]]
        if cleared:
            for k in cleared:
                st[k] = False
            self._save_state(st)
        return ", ".join(reasons) or None

    def live(self):
        """True while <root>/a2/LIVE exists: attempt 2 decides sales; otherwise attempt 1's rules do."""
        return os.path.exists(os.path.join(self.root, "a2", "LIVE"))

    def _mode_text(self):
        if self.live():
            return "LIVE 켜짐: 시도 2의 판매자, 구매자, 재입고 계획이 가게를 운영함. done.policy는 쓰지 않음"
        return (
            "LIVE 꺼짐 (11-07 11시 성수님 결정, Codex 구독 한도 소진): 판매, 구매, 재입고는 시도 1의 규칙이 policy.json 값을 읽어 "
            "결정함. 판매자와 구매자 지침 수정(edits의 seller, buyer), trial, restock_plan은 가게에 반영되지 않아 커널이 받지 않음. "
            "네가 규칙을 바꾸는 길은 done.policy 하나임: policy 도구로 지금 값을 보고, 키 하나씩 허용 폭 안에서 고치고, "
            "근거 숫자를 reason에 적음. 고친 뒤에는 브리핑의 정산, 매출, 이익, 별점으로 효과를 보고 유지하거나 되돌림(되돌리기도 같은 policy 항목). "
            "자기 지침(edits의 operator)과 메모는 지금처럼 고칠 수 있음"
        )

    # ------------------------------------------------------------ tool loop
    def _always_text(self):
        d = os.path.join(self.harness_root, "operator")
        errs = hz.validate_dir(d)
        if errs:
            return None, errs
        h = hz.load(d)
        try:
            with open(os.path.join(d, "settings.json"), encoding="utf-8") as f:
                settings_text = f.read()
        except OSError:
            settings_text = "{}"
        return (h, h.always_text + "\n## settings.json (지금 값, 커널이 범위로 묶음)\n\n" + settings_text), []

    def _market_brief(self):
        try:
            with open(self.market_brief_path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def _tool_list(self):
        return "\n".join(
            f"- {name}: {' '.join((fn.__doc__ or '').split())[:300]}"
            for name, (fn, agents) in tools.TOOLS.items()
            if "operator" in agents
        )

    def _run_tool(self, name, args):
        args = {k: v for k, v in (args if isinstance(args, dict) else {}).items() if k not in RESERVED_ARGS}
        if name in HARNESS_TOOLS:
            args["harness_root"] = self.harness_root
        if name == "replay_probe":
            args["dry"] = self.dry
        try:
            obs = tools.call(name, "operator", self.root, **args)
        except Exception as e:  # a tool fed bad values by the model is an observation, not a crash
            obs = f"도구 오류: {type(e).__name__}: {e}"
        obs = c.cap(c.mask(str(obs), self.secrets), tools.MAX_CHARS)
        shown = {k: v for k, v in args.items() if k not in RESERVED_ARGS}
        self.traj.append(
            "operator",
            "tool",
            name=name,
            args=c.mask(json.dumps(shown, ensure_ascii=False, default=str)[:2000], self.secrets),
            obs_chars=len(obs),
        )
        return obs

    def _ask(self, prompt, route):
        # A2_OPERATOR_EFFORT (11-10, 성수님 결정: 사용량 절감) overrides the harness route's effort when set
        effort = os.environ.get("A2_OPERATOR_EFFORT") or route.get("effort", "medium")
        return self.terra.complete(prompt, agent="operator", timeout_s=route.get("timeout_s", 300), effort=effort)

    def wake(self, reason, now=None):
        """One wake: tool loop until done or max_steps. -> summary dict (also written as an operator_wake row)."""
        now = c.now_ts() if now is None else now
        s, st = self.settings(), self._state()
        ev = self.events_now(s)
        st.update(last_wake_ts=now, last_rank=ev["rank"], shop_missing=ev["shop_missing"],
                  bankrupt_near=ev["bankrupt_near"])
        last_brief = st.get("last_brief_ts")
        st["last_brief_ts"] = now
        self._save_state(st)
        for agent in EDIT_TARGETS:
            versions.ensure_baseline(self.harness_root, agent, now)
        self.trial_timeouts(now)

        loaded, errs = self._always_text()
        if loaded is None:
            return self._end(reason, 0, "operator_harness_invalid", {"errors": errs})
        h, always = loaded
        route = h.routing.get("operator.wake") or {}
        brief = c.cap(b.build(self.root, now, last_brief), BRIEF_CHARS)
        base = "\n\n".join(
            [
                always,
                self._market_brief(),
                "## 브리핑 (커널이 모은 사실, 해석 없음)",
                brief,
                "## 지금 운영 방식",
                self._mode_text(),
                "## 깨운 이유",
                reason,
                "## 도구 (운영자)",
                self._tool_list(),
                "## 답 형식",
                ACTION_SCHEMA,
            ]
        )
        history, used = [], 0
        for step in range(s["max_steps"]):
            last_step = step == s["max_steps"] - 1 or used >= OBS_TOTAL_CHARS
            prompt = base + "\n\n## 이번 점검의 도구 호출과 관찰\n\n" + ("\n\n".join(history) or "아직 없음")
            if last_step:
                prompt += "\n\n도구 단계나 관찰 한도를 다 씀. 이번 답은 반드시 done 객체여야 함."
            try:
                act = parse_last_object(self._ask(prompt, route))
            except (ModelRefused, ModelError) as e:
                return self._end(reason, step + 1, "model_error", {"error": f"{type(e).__name__}: {e}"})
            except ParseError:
                history.append(f"[단계 {step + 1}] 형식 오류: JSON 객체 하나로 답해야 함")
                continue
            if isinstance(act.get("done"), dict):
                result = self.finish(act["done"], prompt, route, now)
                return self._end(reason, step + 1, "done", result)
            if last_step:
                break
            name = act.get("tool")
            obs = self._run_tool(name, act.get("args"))
            used += len(obs)
            history.append(
                f"[단계 {step + 1}] {json.dumps({'tool': name, 'args': act.get('args')}, ensure_ascii=False)[:500]}\n{obs}"
            )
        return self._end(reason, step + 1, "no_done", {})

    def _end(self, reason, steps, status, result):
        self.traj.append("operator", "operator_wake", reason=reason, steps=steps, status=status, result=result)
        return dict(result, status=status, steps=steps)

    # ------------------------------------------------------------ done
    def _apply_edits(self, edits, now):
        applied, errors = [], []
        for e in edits if isinstance(edits, list) else []:
            if not isinstance(e, dict) or e.get("target") not in EDIT_TARGETS:
                errors.append(f"edit must be an object with target in {EDIT_TARGETS}: {str(e)[:200]}")
                continue
            target, files = e["target"], e.get("files")
            reason = c.mask(str(e.get("reason") or ""), self.secrets)[:1000]
            if target in versions.trial_state(self.harness_root):
                errors.append(f"{target}: 동시 비교 중에는 지금 판을 바꾸지 않음. trial_end로 먼저 끝냄")
                continue
            closing = None
            if target in ("seller", "buyer"):
                closing = (b.version_results(self.root, target, now, last=1, harness_root=self.harness_root) or [None])[-1]
            try:
                ok, info = versions.apply_edit(self.harness_root, target, files, reason, self.secrets, now)
            except (OSError, ValueError) as ex:
                ok, info = False, {"errors": [f"{type(ex).__name__}"]}
            if not ok:
                errors += [f"{target}: {x}" for x in info["errors"]]
                continue
            if closing is not None:
                self.traj.append(
                    "operator",
                    "version_result",
                    target=target,
                    version=closing["version"],
                    since_ts=closing["since_ts"],
                    until_ts=now,
                    metrics=closing["metrics"],
                )
            self.traj.append(
                "operator", "harness_edit", target=target, version=info["version"], files=sorted(files), reason=reason
            )
            applied.append({"target": target, "version": info["version"]})
        return applied, errors

    def _reflect(self, items):
        out = []
        for r in items if isinstance(items, list) else []:
            if not isinstance(r, dict) or r.get("target") not in EDIT_TARGETS:
                continue
            try:
                version = int(r.get("version"))
            except (TypeError, ValueError):
                continue
            effects = []
            for e in r.get("effects") or []:
                if isinstance(e, dict):
                    effect = e.get("effect") if e.get("effect") in versions.EFFECTS else "판단 불가"
                    effects.append(
                        {
                            "edit": c.mask(str(e.get("edit") or ""), self.secrets)[:500],
                            "effect": effect,
                            "evidence": c.mask(str(e.get("evidence") or ""), self.secrets)[:500],
                        }
                    )
            lesson = r.get("lesson")
            lesson = c.mask(str(lesson), self.secrets)[:1000] if lesson else None
            self.traj.append(
                "operator", "reflection", target=r["target"], version=version, effects=effects, lesson=lesson
            )
            if lesson:
                versions.write_experience(self.harness_root, r["target"], version, effects, lesson)
            out.append({"target": r["target"], "version": version, "lesson": bool(lesson)})
        return out

    def _revert(self, items, now):
        out = []
        for r in items if isinstance(items, list) else []:
            if not isinstance(r, dict) or r.get("target") not in EDIT_TARGETS:
                continue
            reason = c.mask(str(r.get("reason") or ""), self.secrets)[:500]
            if r["target"] in versions.trial_state(self.harness_root):
                out.append({"target": r["target"], "ok": False, "errors": ["동시 비교 중에는 되돌리지 않음. trial_end로 먼저 끝냄"]})
                continue
            ok, info = versions.revert(self.harness_root, r["target"], r.get("version"), reason or None, now)
            if ok:
                self.traj.append(
                    "operator",
                    "harness_revert",
                    target=r["target"],
                    version=info["version"],
                    restored_from=info["restored_from"],
                    reason=reason,
                )
            out.append({"target": r["target"], "ok": ok, **info})
        return out

    def trial_timeouts(self, now):
        """End each trial older than versions.TRIAL_MAX_H without adopting it (kernel rule): the current version stays.
        -> list of _trial_end results."""
        out = []
        for target, t in sorted(versions.trial_state(self.harness_root).items()):
            if now - (t.get("started_ts") or now) >= versions.TRIAL_MAX_H * 3600:
                reason = f"커널 시간 상한 {versions.TRIAL_MAX_H}시간: 판정 없이 끝내고 지금 판 유지"
                out.append(self._trial_end({"target": target, "adopt": False, "reason": reason}, now))
        return out

    def _trial_end(self, item, now):
        if not isinstance(item, dict) or item.get("target") not in versions.TRIAL_AGENTS:
            return {"ok": False, "errors": [f"trial_end.target must be one of {versions.TRIAL_AGENTS}"]}
        target, adopt = item["target"], item.get("adopt") is True
        reason = c.mask(str(item.get("reason") or ""), self.secrets)[:500]
        t = versions.trial_state(self.harness_root).get(target)
        closing = b.trial_results(self.root, now, harness_root=self.harness_root) if t else None
        ok, info = versions.end_trial(self.harness_root, target, adopt, reason or None, now)
        if not ok:
            return {"ok": False, **info}
        tr = info["trial"]
        self.traj.append(
            "operator", "trial_end", target=target, version=tr["version"], base=tr["base"], adopt=adopt,
            reason=reason, metrics=(closing or {}).get("arms"),
        )
        if adopt:
            base = (b.version_results(self.root, target, now, last=1, harness_root=self.harness_root) or [None])[-1]
            if base is not None:
                self.traj.append(
                    "operator", "version_result", target=target, version=base["version"], since_ts=base["since_ts"],
                    until_ts=now, metrics=base["metrics"],
                )
            self.traj.append(
                "operator", "harness_edit", target=target, version=info["version"], files=tr.get("files") or [],
                reason=f"동시 비교 채택(후보 판 {tr['version']}): {reason}"[:1000], adopted_from=tr["version"],
            )
        return {"ok": True, "adopted": adopt, "version": info.get("version"), "trial_version": tr["version"]}

    def _trial_start(self, item, now):
        if not isinstance(item, dict) or item.get("target") not in versions.TRIAL_AGENTS:
            return {"ok": False, "errors": [f"trial.target must be one of {versions.TRIAL_AGENTS}"]}
        if not versions.TRIAL_ENABLED:
            return {"ok": False, "errors": ["동시 비교는 꺼져 있음. 판매자 수정은 edits로 바로 적용하고 약 1시간 뒤 결과로 판단함"]}
        reason = c.mask(str(item.get("reason") or ""), self.secrets)[:1000]
        try:
            ok, info = versions.start_trial(
                self.harness_root, item["target"], item.get("files"), item.get("share"), reason, self.secrets, now
            )
        except (OSError, ValueError) as ex:
            ok, info = False, {"errors": [type(ex).__name__]}
        if ok:
            self.traj.append(
                "operator", "trial_start", target=item["target"], version=info["version"], base=info["base"],
                share=info["share"], files=sorted(item.get("files") or {}), reason=reason,
            )
        return {"ok": ok, **info}

    def _restock(self, plan, now):
        if not isinstance(plan, dict):
            return {"ok": False, "error": "restock_plan must be an object"}
        buy = []
        for x in plan.get("buy") or []:
            if (
                isinstance(x, dict)
                and isinstance(x.get("sku"), str)
                and isinstance(x.get("qty"), int)
                and not isinstance(x.get("qty"), bool)
                and x["qty"] > 0
            ):
                buy.append({"sku": x["sku"], "qty": x["qty"]})
        vu = plan.get("valid_until")
        if not isinstance(vu, (int, float)) or isinstance(vu, bool):
            return {"ok": False, "error": "valid_until must be a unix time"}
        doc = {"ts": round(now, 3), "buy": buy, "valid_until": vu}
        path = os.path.join(self.root, "a2/restock_plan.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
        self.traj.append("operator", "restock_plan", buy=buy, valid_until=vu)
        return {"ok": True, "buy": len(buy)}

    def _forum(self, posts):
        posts = [
            {"post": c.mask(p["post"], self.secrets), "as": p["as"]}
            for p in (posts if isinstance(posts, list) else [])
            if isinstance(p, dict) and isinstance(p.get("post"), str) and p.get("as") in ("shop", "buyer")
        ]
        if not posts:
            return {"queued": 0}
        try:
            forum = importlib.import_module("kernel.forum")
        except ImportError:
            self.traj.append("operator", "forum", status="unavailable", n=len(posts))
            return {"queued": 0, "status": "forum unavailable"}
        res = forum.queue(self.root, posts)  # logs one row per post itself
        return {"queued": sum(1 for r in res or [] if isinstance(r, dict) and r.get("ok")), "result": res}

    def _memcheck(self):
        if self.dry:
            self.traj.append("operator", "memcheck", status="dry")
            return {"started": False}
        self.spawn(
            [sys.executable, "-m", "kernel.memcheck", "--root", self.root],
            cwd=HERE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.traj.append("operator", "memcheck", status="started")
        return {"started": True}

    def _drop_unused(self, done, result):
        """LIVE off: attempt 2's seller and buyer harness, trials and restock plans do not reach the shop."""
        done = dict(done)
        dropped = []
        edits = done.get("edits")
        if isinstance(edits, list):
            keep = [e for e in edits if not (isinstance(e, dict) and e.get("target") in ("seller", "buyer"))]
            if len(keep) != len(edits):
                dropped.append(f"edits seller/buyer {len(edits) - len(keep)}개")
            done["edits"] = keep
        for k in ("trial", "trial_end", "restock_plan", "revert"):
            if k in done and (k != "revert" or any(
                isinstance(r, dict) and r.get("target") in ("seller", "buyer") for r in done.get("revert") or []
            )):
                if k == "revert":
                    done["revert"] = [r for r in done["revert"] if not (isinstance(r, dict) and r.get("target") in ("seller", "buyer"))]
                else:
                    done.pop(k)
                dropped.append(k)
        if dropped:
            result["not_applied_live_off"] = dropped
        return done

    def _policy(self, items, now):
        if self.live():
            return [{"ok": False, "error": "LIVE 켜짐: policy는 시도 1 규칙이 운영할 때만 고침"}]
        cats = {v.get("category") for v in b._catalog(self.root).values() if isinstance(v, dict) and v.get("category")}
        return policy_edit.apply(self.root, items, self.traj, now, categories=cats)

    def finish(self, done, prompt, route, now):
        """Apply a done action. Edit format errors go back to the model once for one corrected try."""
        result = {"note": c.mask(str(done.get("note") or ""), self.secrets)[:500]}
        result["reflection"] = self._reflect(done.get("reflection"))
        if not self.live():
            done = self._drop_unused(done, result)
        if "trial_end" in done:
            result["trial_end"] = self._trial_end(done["trial_end"], now)
        result["revert"] = self._revert(done.get("revert"), now)
        applied, errors = self._apply_edits(done.get("edits"), now)
        result["edit_errors"] = errors
        if errors:
            retry_prompt = (
                prompt
                + "\n\n## 방금 낸 done\n\n"
                + json.dumps({"edits": done.get("edits")}, ensure_ascii=False)[:20_000]
                + "\n\n## 형식 검사 오류 (한 번 더 고칠 수 있음)\n\n"
                + "\n".join(f"- {e}" for e in errors)
                + '\n\n걸린 수정만 고쳐 {"done": {"edits": [...]}}로 답함. 고치지 않으려면 edits를 빈 목록으로 줌.'
            )
            try:
                again = parse_last_object(self._ask(retry_prompt, route)).get("done") or {}
                more, result["retry_errors"] = self._apply_edits(again.get("edits"), now)
                applied += more
            except (ModelRefused, ModelError, ParseError) as e:
                result["retry_errors"] = [f"retry failed: {type(e).__name__}"]
        result["edits"] = applied
        if "trial" in done:
            result["trial"] = self._trial_start(done["trial"], now)
        if isinstance(done.get("settings"), dict):
            cur = c.load_json(os.path.join(self.harness_root, "operator", "settings.json"), {}) or {}
            new = dict(cur, **done["settings"])
            ok, info = versions.apply_edit(
                self.harness_root,
                "operator",
                {"settings.json": json.dumps(new, ensure_ascii=False, indent=1)},
                "운영자 설정 변경",
                self.secrets,
                now,
            )
            if ok:
                self.traj.append(
                    "operator", "harness_edit", target="operator", version=info["version"], files=["settings.json"],
                    reason="운영자 설정 변경",
                )
            result["settings"] = {"ok": ok, **info, "bounded": bounded(new)}
        if "restock_plan" in done:
            result["restock_plan"] = self._restock(done["restock_plan"], now)
        if "forum" in done:
            result["forum"] = self._forum(done["forum"])
        if "policy" in done:
            result["policy"] = self._policy(done["policy"], now)
        if done.get("memcheck") is True:
            result["memcheck"] = self._memcheck()
        return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--once", action="store_true", help="one wake now, then exit")
    ap.add_argument("--dry", action="store_true", help="stub model that answers done with no edits")
    ap.add_argument("--harness", default=HARNESS)
    ap.add_argument("--cli", default=CLI)
    a = ap.parse_args(argv)
    root = c.data_root(a.root)
    login = c.owner_login(root)
    secrets = (login,) if login else ()
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets=secrets)
    if a.dry:
        terra = DryTerra()
    else:
        from kernel.llm import Terra

        terra = Terra(traj, cli=a.cli, secrets=secrets)
    op = Operator(root, terra, traj, harness_root=a.harness, dry=a.dry)
    while True:
        versions.check_breakage(root, a.harness, traj, op.settings()["breakage_n"])
        reason = "수동 실행 (--once)" if a.once else op.wake_reason()
        if reason:
            try:
                print(json.dumps(op.wake(reason), ensure_ascii=False, default=str), flush=True)
            except Exception as ex:  # one bad wake must not stop the daemon
                if a.once:
                    raise
                traj.append("operator", "operator_wake", reason=reason, status="crash", error=type(ex).__name__)
        if a.once:
            return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
