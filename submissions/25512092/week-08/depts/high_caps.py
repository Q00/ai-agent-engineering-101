"""The ceilings of the automatic high-price split rules (QC daily rule depts/qc.py, CEO rule depts/ceo_policy.py):
high_share and high_max never go above these. high_caps.json, read every run; default 0.6 / 40 (11/09 ~11:00).
11/09 ~14:35 (person, "고가 집중"): 0.7 / 60 from the 15:05 check on (tools/high_focus_rollout.py); the high-price
brake (tools/high_focus_rollout.py) sets 0.6 / 40 again together with the policy."""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "high_caps.json")
DEFAULT = (0.6, 40)


def get():
    try:
        with open(PATH, encoding="utf-8") as f:
            j = json.load(f)
        return float(j["share_cap"]), int(j["max_cap"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT


def set_caps(share_cap, max_cap, why=""):
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump({"share_cap": share_cap, "max_cap": max_cap, "why": why}, f, ensure_ascii=False)
