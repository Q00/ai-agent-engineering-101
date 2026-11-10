"""L1 policy (RL_PROACTIVE.md 2.1): a contextual bandit that picks one parameter of a rule, no LLM.

Thompson sampling on the mean reward per (context, arm), old rewards fading with a half-life of 8 h. The reward is
board score (score_model.delta), so every arm is judged in the unit the round is ranked in.

Limits, all from the design:
  - exploration is capped: with probability 1 - explore the arm with the best observed mean plays (the default arm
    until an arm has MIN_WEIGHT of evidence), and only with probability `explore` (0.15) a Thompson draw plays;
  - arms are small steps around the constant in use (the caller keeps them within +-20%);
  - L0 stays in force: the caller clamps whatever the arm gives to its floors.
Every choice is logged with its context, arm and propensity (logs/r3/decisions.jsonl, synced to Supabase
`decisions`) so a logged policy can be evaluated offline by inverse propensity scoring (ips()).
"""
import json
import math
import random
import time

from . import config

HALF_LIFE_H = 8.0
EXPLORE = 0.15
MIN_WEIGHT = 5.0                      # decayed observations an arm needs before its mean can make it the greedy arm
DRAWS = 200                           # Monte Carlo draws for the Thompson share of the propensity


def _key(context):
    return context if isinstance(context, str) else "|".join(str(x) for x in context)


class Bandit:
    """state: {context: {arm: [weight, sum, sum of squares, ts]}} kept in Memory meta under `bandit:<name>`.
    One process owns one bandit (the shop its ask bandit, the org server the clearance bandit)."""

    def __init__(self, name, arms, default, mem=None, half_life_h=HALF_LIFE_H, explore=EXPLORE, rng=None, log=True):
        assert default in arms
        self.name, self.arms, self.default, self.mem = name, list(arms), default, mem
        self.half_life_s, self.explore, self.rng, self.log = half_life_h * 3600, explore, rng or random.Random(), log
        self.state = (mem.get(f"bandit:{name}") if mem else None) or {}

    # ------------------------------------------------------------------------------------- evidence
    def _stats(self, context, now):
        """{arm: (weight, mean, variance)} with every observation faded to now."""
        out = {}
        for a in self.arms:
            w, s, q, ts = (self.state.get(_key(context)) or {}).get(str(a)) or (0.0, 0.0, 0.0, now)
            f = 0.5 ** (max(0.0, now - ts) / self.half_life_s)
            w, s, q = w * f, s * f, q * f
            mean = s / w if w > 0 else 0.0
            out[a] = (w, mean, max(0.0, q / w - mean * mean) if w > 0 else 0.0)
        return out

    def greedy(self, stats):
        known = {a: m for a, (w, m, _) in stats.items() if w >= MIN_WEIGHT}
        if self.default not in known:
            return self.default                              # the constant in use, until it has been measured itself
        return max(known, key=lambda a: (known[a], a == self.default))

    def _draw(self, stats, rng):
        tw = sum(w for w, _, _ in stats.values())
        pooled_mean = sum(w * m for w, m, _ in stats.values()) / tw if tw else 0.0
        pooled_var = sum(w * (v + (m - pooled_mean) ** 2) for w, m, v in stats.values()) / tw if tw else 0.0
        sd = math.sqrt(pooled_var) or 1e-6
        best, best_v = None, None
        for a, (w, m, _) in stats.items():
            v = (m if w > 0 else pooled_mean) + sd / math.sqrt(w + 1.0) * rng.gauss(0, 1)
            if best is None or v > best_v:
                best, best_v = a, v
        return best

    # ------------------------------------------------------------------------------------- choose / learn
    def choose(self, context, now=None, **note):
        """(arm, propensity). propensity = (1 - explore) * [arm is greedy] + explore * P(Thompson draws arm)."""
        now = time.time() if now is None else now
        stats = self._stats(context, now)
        g = self.greedy(stats)
        explored = self.rng.random() < self.explore
        arm = self._draw(stats, self.rng) if explored else g
        mc = random.Random(hash((self.name, _key(context), round(now))) & 0xFFFFFFFF)
        share = sum(self._draw(stats, mc) == arm for _ in range(DRAWS)) / DRAWS
        prop = (1 - self.explore) * (arm == g) + self.explore * share
        prop = max(prop, self.explore / (DRAWS * len(self.arms)))       # never 0: the arm was just played
        if self.log:
            _log({"purpose": f"bandit:{self.name}", "context": _key(context), "arm": arm, "propensity": round(prop, 4),
                  "greedy": g, "explored": explored, **note})
        return arm, prop

    def update(self, context, arm, reward, now=None, **note):
        now = time.time() if now is None else now
        if self.mem:                                         # another run of the same owner may have written since
            self.state = self.mem.get(f"bandit:{self.name}") or self.state
        cell = self.state.setdefault(_key(context), {})
        w, s, q, ts = cell.get(str(arm)) or (0.0, 0.0, 0.0, now)
        f = 0.5 ** (max(0.0, now - ts) / self.half_life_s)
        cell[str(arm)] = [w * f + 1.0, s * f + reward, q * f + reward * reward, now]
        if self.mem:
            self.mem.put(f"bandit:{self.name}", self.state)
        if self.log:
            _log({"purpose": f"bandit_reward:{self.name}", "context": _key(context), "arm": arm, "reward": reward,
                  **note})

    def summary(self, now=None):
        now = time.time() if now is None else now
        return {k: {str(a): {"n": round(w, 1), "mean": m} for a, (w, m, _) in self._stats(k, now).items() if w > 0}
                for k in self.state}


def ips(rows, policy):
    """Offline value of `policy(context) -> arm` from logged rows [{context, arm, propensity, reward}]: the mean of
    reward * [policy picks the logged arm] / propensity (self-normalised). None without a matching row."""
    num = den = 0.0
    for r in rows:
        if r.get("propensity") and policy(r["context"]) == r["arm"]:
            num += r["reward"] / r["propensity"]
            den += 1.0 / r["propensity"]
    return num / den if den else None


def _log(row):
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_DIR / "decisions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), **row}, ensure_ascii=False) + "\n")
    except OSError:
        pass
