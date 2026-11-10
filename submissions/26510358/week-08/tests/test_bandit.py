"""RL_PROACTIVE.md 2.1 and 3: the bandit explores at most its share, keeps the constant in use until it has
evidence, fades old rewards, logs a usable propensity; initiative.decide gives act / wait / skip."""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import bandit, config, initiative


class Meta:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return json.loads(self.d[k]) if k in self.d else default

    def put(self, k, v):
        self.d[k] = json.dumps(v)


def make(mem=None, seed=1, **kw):
    return bandit.Bandit("t", [0.9, 1.0, 1.1], 1.0, mem=mem, rng=random.Random(seed), log=False, **kw)


def test_without_evidence_the_constant_in_use_plays_outside_the_exploration_share():
    b = make()
    picks = [b.choose("goods|A", now=1000.0 + i)[0] for i in range(2000)]
    assert 0.80 <= picks.count(1.0) / len(picks) <= 0.95          # 85% greedy plus a third of the draws


def test_it_moves_to_the_arm_that_pays_and_keeps_exploring_within_the_cap():
    b, rng, now = make(), random.Random(7), 1000.0
    pay = {0.9: 1.0, 1.0: 2.0, 1.1: 3.0}
    for i in range(600):
        arm, prop = b.choose("goods|A", now=now + i)
        assert 0 < prop <= 1
        b.update("goods|A", arm, pay[arm] + rng.gauss(0, 0.5), now=now + i)
    late = [b.choose("goods|A", now=now + 600)[0] for _ in range(1000)]
    assert late.count(1.1) / 1000 >= 0.85
    assert b.greedy(b._stats("goods|A", now + 600)) == 1.1
    assert b.greedy(b._stats("food|B", now + 600)) == 1.0          # another context has learned nothing


def test_old_rewards_fade_with_the_half_life():
    b = make()
    for _ in range(8):
        b.update("c", 1.1, 5.0, now=0.0)
    w0 = b._stats("c", 0.0)[1.1][0]
    w8 = b._stats("c", 8 * 3600.0)[1.1][0]
    assert w0 == 8 and abs(w8 - 4.0) < 1e-9
    assert b.greedy(b._stats("c", 0.0)) == 1.0                     # the default itself is unmeasured: it stays


def test_state_survives_in_memory_meta_and_the_log_row_carries_the_propensity(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    mem = Meta()
    b = bandit.Bandit("ask", [0.9, 1.0, 1.1], 1.0, mem=mem, rng=random.Random(3))
    arm, prop = b.choose(("goods", "A"), now=10.0, sku="G1")
    b.update(("goods", "A"), arm, 0.25, now=11.0, sku="G1")
    again = bandit.Bandit("ask", [0.9, 1.0, 1.1], 1.0, mem=mem, log=False)
    assert again._stats("goods|A", 11.0)[arm][0] == 1.0
    rows = [json.loads(x) for x in (tmp_path / "decisions.jsonl").read_text().splitlines()]
    assert rows[0]["purpose"] == "bandit:ask" and rows[0]["context"] == "goods|A" and rows[0]["propensity"] == round(prop, 4)
    assert rows[1]["purpose"] == "bandit_reward:ask" and rows[1]["reward"] == 0.25 and rows[1]["sku"] == "G1"


def test_ips_values_a_policy_from_logged_choices():
    rows = [{"context": "c", "arm": 1.0, "propensity": 0.9, "reward": 1.0},
            {"context": "c", "arm": 1.1, "propensity": 0.1, "reward": 3.0},
            {"context": "c", "arm": 1.0, "propensity": 0.9, "reward": 1.0}]
    assert bandit.ips(rows, lambda c: 1.1) == 3.0 and bandit.ips(rows, lambda c: 1.0) == 1.0
    assert bandit.ips(rows, lambda c: 0.9) is None


def test_decide_acts_waits_or_skips_around_the_horvitz_threshold():
    d = initiative.decide
    assert d(0.6, 1.0, 1.0) == ("act", 0.5)
    assert d(0.45, 1.0, 1.0)[0] == "wait" and d(0.39, 1.0, 1.0)[0] == "skip"
    assert d(0.55, 1.0, 1.0, reversible=False)[0] == "wait" and d(0.61, 1.0, 1.0, reversible=False)[0] == "act"
    assert d(0.9, 0.0, 1.0) == ("skip", 1.0)                        # nothing to gain
    assert d(0.01, 1.0, 0.0)[0] == "act"                            # nothing to lose


def test_judge_logs_goal_p_b_c_metric_before_the_action(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    row = initiative.judge("shop_markdown", 0.7, 2e-6, 1e-6, "sku hours of cover, revenue pct", sku="G1", now=5)
    no = initiative.refuse("paid_dislike", "8.6M for the second dislike buys no board score", "forum", C=1.6e-3, now=6)
    logged = [json.loads(x) for x in (tmp_path / "initiative.jsonl").read_text().splitlines()]
    assert logged == [row, no]
    assert row["decision"] == "act" and {"goal", "p", "B", "C", "metric", "p_star"} <= set(row) and row["sku"] == "G1"
    assert no["decision"] == "skip" and no["reason"]
